"""Create controlled synthetic writes before a baseline restore rehearsal."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.proposals import ProposalService
from app.core.database import SessionLocal
from app.models import BuildPlanItem, Material, Project, ProjectReservation, User
from app.schemas.agent import (
    ProposeBuildMaterialReservationArgs,
    ProposeInventoryReservationArgs,
)
from app.services.build_readiness import BuildReadinessService
from scripts.verify_portfolio_database import verify


def rehearse(expected_admin: str) -> dict:
    verification = verify(expected_admin)
    if not verification["portfolio_database_verified"]:
        raise SystemExit("Refusing mutation: database is not the verified synthetic baseline")

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == expected_admin))
        projects = list(
            db.scalars(
                select(Project)
                .where(Project.product_revision_id.is_not(None))
                .order_by(Project.id)
            ).all()
        )
        candidate = None
        for project in projects:
            result = BuildReadinessService(db).analyze(
                project.product_revision_id,
                1,
                project.id,
            )
            if (
                result["sufficient"]
                and result["material_blocker_count"] == 0
                and any(
                    Decimal(str(item["additional_reservation_required"])) > 0
                    for item in result["items"]
                )
            ):
                candidate = project
                break
        if candidate is None:
            raise SystemExit("No sufficient linked Product/Project build scenario is available")

        build_outcome = ProposalService(
            db,
            user,
            "portfolio-restore-rehearsal-build-create",
            client_operation_id="portfolio-restore-rehearsal-build-v1",
        ).create_build_plan_reservation(
            ProposeBuildMaterialReservationArgs(
                product_revision_id=candidate.product_revision_id,
                project_id=candidate.id,
                build_quantity=1,
                reason="Synthetic baseline restore rehearsal",
            )
        )
        build_plan = build_outcome["plan"]
        build_proposal = build_outcome["proposal"]
        if build_proposal is None:
            raise SystemExit("Selected build scenario was already fully reserved")
        ProposalService(
            db,
            user,
            "portfolio-restore-rehearsal-build-approve",
        ).approve(build_proposal.id)

        build_material_ids = set(
            db.scalars(
                select(BuildPlanItem.material_id).where(
                    BuildPlanItem.build_plan_id == build_plan.id
                )
            ).all()
        )
        manual_material = db.scalar(
            select(Material)
            .where(
                Material.id.not_in(build_material_ids),
                Material.is_active.is_(True),
                Material.is_deleted.is_(False),
                (Material.quantity - Material.reserved_quantity) >= 1,
            )
            .order_by(Material.id)
        )
        if manual_material is None:
            raise SystemExit(
                "No independent material is available for manual reservation rehearsal"
            )
        manual_project = db.scalar(
            select(Project).where(Project.id != candidate.id).order_by(Project.id)
        )
        manual_proposal = ProposalService(
            db,
            user,
            "portfolio-restore-rehearsal-manual-create",
            client_operation_id="portfolio-restore-rehearsal-manual-v1",
        ).create_reservation(
            ProposeInventoryReservationArgs(
                project_id=manual_project.id,
                items=[{"material_id": manual_material.id, "quantity": "1"}],
                reason="Synthetic baseline restore rehearsal",
            )
        )
        ProposalService(
            db,
            user,
            "portfolio-restore-rehearsal-manual-approve",
        ).approve(manual_proposal.id)

        return {
            "synthetic_mutations_completed": True,
            "build_plan_id": build_plan.id,
            "build_plan_proposal_id": build_proposal.id,
            "manual_reservation_proposal_id": manual_proposal.id,
            "executed_proposal_count": int(
                db.scalar(
                    select(func.count()).select_from(type(build_proposal)).where(
                        type(build_proposal).status == "executed"
                    )
                )
                or 0
            ),
            "project_reservation_count_after_mutation": int(
                db.scalar(select(func.count()).select_from(ProjectReservation)) or 0
            ),
            "inventory_writes_required_human_approval": True,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-admin", required=True)
    parser.add_argument("--confirm-synthetic-mutation", action="store_true")
    args = parser.parse_args()
    if not args.confirm_synthetic_mutation:
        raise SystemExit("Pass --confirm-synthetic-mutation explicitly.")
    print(json.dumps(rehearse(args.expected_admin), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
