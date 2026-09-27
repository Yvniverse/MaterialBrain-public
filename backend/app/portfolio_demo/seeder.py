from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import BusinessError
from app.models import (
    BomItem,
    Category,
    InventoryLot,
    Location,
    Material,
    Project,
    ProjectReservation,
    StockMovement,
    User,
)
from app.services.inventory import InventoryService
from app.services.location_organizers import (
    organizer_bin_note,
    organizer_child_name,
    organizer_slot_names,
)
from app.services.warehouse_maps import WarehouseMapService

DATASET_VERSION = "v2"
DATASET_DIR = Path(__file__).resolve().parents[2] / "portfolio_demo_data" / DATASET_VERSION
DEMO_MARKER = "Portfolio Demo v2"

CATEGORY_CODE_BY_HINT = {
    "MCU": "CAT-01-01",
    "ADC": "CAT-01-02",
    "运放": "CAT-01-04",
    "电源管理": "CAT-01-06",
    "通信接口": "CAT-01-07",
    "逻辑芯片": "CAT-01-09",
    "驱动芯片": "CAT-01-10",
    "传感器芯片": "CAT-01-12",
    "贴片电阻": "CAT-02-01",
    "陶瓷电容": "CAT-03-01",
    "三极管 / MOS": "CAT-06",
    "连接器": "CAT-07",
    "传感器": "CAT-08",
    "模块": "CAT-09",
    "开发板 / PCB": "CAT-12",
}

CATEGORY_CODE_BY_INFERRED = {
    "sensor": "CAT-08",
    "resistor": "CAT-02-01",
    "capacitor": "CAT-03-01",
    "connector": "CAT-07",
    "cable_rf": "CAT-13",
    "antenna": "CAT-13",
    "power_ic": "CAT-01-06",
    "mcu": "CAT-01-01",
    "inductor_ferrite": "CAT-04",
    "audio_ic": "CAT-01-13",
    "transistor": "CAT-06",
    "mosfet": "CAT-06",
    "logic_ic": "CAT-01-09",
    "protection": "CAT-05",
    "crystal": "CAT-10",
    "lab_tool": "CAT-15",
    "interface_ic": "CAT-01-07",
    "unclassified": "CAT-16",
    "misc_hardware": "CAT-16",
}


def _load(name: str) -> Any:
    return json.loads((DATASET_DIR / name).read_text(encoding="utf-8"))


def _fold(value: Any) -> str:
    return str(value or "").strip().casefold()


def _seed_key(kind: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]
    return f"portfolio-v2-{kind}-{digest}"


def _portfolio_metadata(*, source_ref: str, identity_source: str, confidence: str) -> dict:
    return {
        "dataset_version": DATASET_VERSION,
        "source_ref": source_ref,
        "identity_source": identity_source,
        "catalog_confidence": confidence,
        "stock_is_synthetic": True,
    }


@dataclass(frozen=True)
class MaterialSeed:
    source_ref: str
    code: str
    name: str
    mpn: str
    specification: str
    package: str
    manufacturer: str
    supplier_part_number: str
    quantity: Decimal
    safety_stock: Decimal
    target_stock: Decimal
    unit_price: Decimal
    primary_location_code: str | None
    lots: tuple[dict[str, Any], ...]
    tags: tuple[str, ...]
    attributes: dict[str, Any]
    category_code: str
    confidence: str
    identity_source: str
    notes: str


@dataclass
class SeedSummary:
    dry_run: bool
    input_material_identities: int = 0
    created_materials: int = 0
    reused_by_code: int = 0
    reused_by_mpn: int = 0
    enriched_existing_materials: int = 0
    created_locations: int = 0
    created_projects: int = 0
    created_bom_items: int = 0
    initialized_stock_items: int = 0
    initialized_lot_distributions: int = 0
    initialized_reservations: int = 0
    warehouse_map_seeded: bool = False
    warehouse_map_code: str = ""
    warehouse_graph_hash: str = ""
    ambiguous_mpn_choices: list[dict[str, Any]] = field(default_factory=list)
    low_confidence_unclassified: list[dict[str, str]] = field(default_factory=list)
    material_mappings: list[dict[str, Any]] = field(default_factory=list)
    database_counts: dict[str, int] = field(default_factory=dict)
    invariants: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_version": DATASET_VERSION,
            "dry_run": self.dry_run,
            "input_material_identities": self.input_material_identities,
            "created_materials": self.created_materials,
            "reused_by_code": self.reused_by_code,
            "reused_by_mpn": self.reused_by_mpn,
            "enriched_existing_materials": self.enriched_existing_materials,
            "created_locations": self.created_locations,
            "created_projects": self.created_projects,
            "created_bom_items": self.created_bom_items,
            "initialized_stock_items": self.initialized_stock_items,
            "initialized_lot_distributions": self.initialized_lot_distributions,
            "initialized_reservations": self.initialized_reservations,
            "warehouse_map_seeded": self.warehouse_map_seeded,
            "warehouse_map_code": self.warehouse_map_code,
            "warehouse_graph_hash": self.warehouse_graph_hash,
            "ambiguous_mpn_choices": self.ambiguous_mpn_choices,
            "low_confidence_unclassified": self.low_confidence_unclassified,
            "material_mappings": self.material_mappings,
            "database_counts": self.database_counts,
            "invariants": self.invariants,
        }


class PortfolioDemoV2Seeder:
    """Additive, deduplicated portfolio data integration for a live database."""

    def __init__(self, db: Session, operator: User, config: Settings):
        self.db = db
        self.operator = operator
        self.config = config
        self.v1_locations = _load("v1_demo_locations.json")
        self.extension_locations = _load("demo_locations_extension.json")
        self.manifest = _load("MANIFEST.json")
        self.v1_materials = _load("v1_demo_materials.json")
        self.catalog_materials = _load("lcsc_statement_catalog_extracted.json")
        self.v1_projects = _load("v1_demo_projects_bom.json")
        self.extension_projects = _load("demo_projects_extension.json")
        self._validate_dataset()
        self.material_seeds = self._material_seeds()

    def _validate_dataset(self) -> None:
        expected = {
            "v1_synthetic_materials": len(self.v1_materials),
            "statement_unique_lcsc_codes": len(self.catalog_materials),
            "v1_projects": len(self.v1_projects["projects"]),
            "new_projects": len(self.extension_projects["projects"]),
            "v1_bom_rows": sum(
                len(rows)
                for project in self.v1_projects["projects"]
                for rows in project["versions"].values()
            ),
            "new_bom_rows": sum(
                len(rows)
                for project in self.extension_projects["projects"]
                for rows in project["versions"].values()
            ),
        }
        mismatches = {
            key: {"manifest": self.manifest.get(key), "actual": value}
            for key, value in expected.items()
            if self.manifest.get(key) != value
        }
        lcsc_codes = [item["lcsc_code"] for item in self.catalog_materials]
        v1_codes = [item["code"] for item in self.v1_materials]
        if len(set(lcsc_codes)) != len(lcsc_codes) or len(set(v1_codes)) != len(v1_codes):
            mismatches["material_identity_uniqueness"] = False
        forbidden_accounting_fields = {
            "order_no",
            "logistics_no",
            "payment_amount",
            "purchase_quantity",
            "accounting_amount",
        }
        for item in self.catalog_materials:
            leaked = forbidden_accounting_fields.intersection(item)
            lot_total = sum(Decimal(str(lot["quantity"])) for lot in item.get("lots") or [])
            if leaked:
                mismatches[f"forbidden_fields:{item['lcsc_code']}"] = sorted(leaked)
            if lot_total > Decimal(str(item["demo_quantity"])):
                mismatches[f"lot_total:{item['lcsc_code']}"] = str(lot_total)
        for item in self.v1_materials:
            lot_total = sum(Decimal(str(lot["quantity"])) for lot in item.get("lots") or [])
            if lot_total > Decimal(str(item["quantity"])):
                mismatches[f"lot_total:{item['code']}"] = str(lot_total)
        if mismatches:
            raise BusinessError(
                "PORTFOLIO_DATASET_INVALID",
                "Portfolio Demo v2 数据包完整性验证失败",
                details=mismatches,
            )

    def dry_run(self) -> dict[str, Any]:
        summary = SeedSummary(dry_run=True, input_material_identities=len(self.material_seeds))
        self._plan_material_mappings(summary)
        summary.database_counts = self._database_counts()
        summary.invariants = self._invariants()
        return summary.as_dict()

    def seed(self) -> dict[str, Any]:
        if not self.config.portfolio_demo_seed_enabled:
            raise BusinessError(
                "PORTFOLIO_DEMO_SEED_DISABLED",
                "必须显式设置 PORTFOLIO_DEMO_SEED_ENABLED=true 才能写入 Demo 数据",
                403,
            )

        summary = SeedSummary(dry_run=False, input_material_identities=len(self.material_seeds))
        location_by_code = self._ensure_locations(summary)
        warehouse_map = WarehouseMapService(self.db).seed_from_file(
            str(DATASET_DIR / "warehouse_map_v1.json"),
            verified_by_id=self.operator.id,
            activate=True,
        )
        summary.warehouse_map_seeded = True
        summary.warehouse_map_code = warehouse_map.code
        summary.warehouse_graph_hash = warehouse_map.graph_hash
        category_by_code = {item.code: item for item in self.db.scalars(select(Category)).all()}
        missing_categories = sorted(
            {seed.category_code for seed in self.material_seeds} - set(category_by_code)
        )
        if missing_categories:
            raise BusinessError(
                "PORTFOLIO_CATEGORY_MISSING",
                "Demo 数据引用的分类不存在",
                details={"category_codes": missing_categories},
            )

        mapping, owned_seeds = self._ensure_materials(
            summary,
            location_by_code=location_by_code,
            category_by_code=category_by_code,
        )
        inventory = InventoryService(self.db, self.operator.id, "portfolio-demo-v2")
        self._initialize_inventory(summary, inventory, owned_seeds, location_by_code)
        projects = self._ensure_projects_and_boms(summary, mapping)
        self._ensure_reservations(summary, inventory, projects, mapping)
        summary.database_counts = self._database_counts()
        summary.invariants = self._invariants()
        if not all(summary.invariants.values()):
            raise BusinessError(
                "PORTFOLIO_INVARIANT_FAILED",
                "Portfolio Demo v2 写入后数据库不变量验证失败",
                details=summary.invariants,
            )
        return summary.as_dict()

    def _material_seeds(self) -> list[MaterialSeed]:
        seeds: list[MaterialSeed] = []
        for item in self.v1_materials:
            attributes = dict(item.get("attributes") or {})
            attributes["portfolio_demo"] = _portfolio_metadata(
                source_ref=item["code"],
                identity_source="synthetic_v1",
                confidence="high",
            )
            seeds.append(
                MaterialSeed(
                    source_ref=item["code"],
                    code=item["code"],
                    name=item["name"],
                    mpn=item.get("mpn") or "",
                    specification=item.get("specification") or "",
                    package=item.get("package") or "",
                    manufacturer=item.get("manufacturer") or "",
                    supplier_part_number="",
                    quantity=Decimal(str(item["quantity"])),
                    safety_stock=Decimal(str(item["safety_stock"])),
                    target_stock=Decimal(str(item["target_stock"])),
                    unit_price=Decimal(str(item.get("unit_price") or 0)),
                    primary_location_code=item.get("primary_location_code"),
                    lots=tuple(item.get("lots") or []),
                    tags=tuple(item.get("tags") or []),
                    attributes=attributes,
                    category_code=CATEGORY_CODE_BY_HINT[item["category_hint"]],
                    confidence="high",
                    identity_source="synthetic_v1",
                    notes=item.get("notes") or "",
                )
            )

        for item in self.catalog_materials:
            confidence = str(item.get("category_confidence") or "low").casefold()
            high_confidence = confidence == "high"
            attributes = dict(item.get("attributes") or {}) if high_confidence else {}
            attributes["portfolio_demo"] = _portfolio_metadata(
                source_ref=item["lcsc_code"],
                identity_source="lcsc_catalog_identity",
                confidence=confidence,
            )
            inferred = str(item.get("inferred_category") or "unclassified")
            seeds.append(
                MaterialSeed(
                    source_ref=item["lcsc_code"],
                    code=item["lcsc_code"],
                    name=(item["model"] if high_confidence else f"{item['model']}（待分类）"),
                    mpn=item["model"],
                    specification=(
                        "LCSC catalog identity; warehouse stock is maintained independently."
                        if high_confidence
                        else "Catalog identity only; technical classification pending."
                    ),
                    package=(item.get("package_hint") or "") if high_confidence else "",
                    manufacturer="",
                    supplier_part_number=item["lcsc_code"],
                    quantity=Decimal(str(item["demo_quantity"])),
                    safety_stock=Decimal(str(item["demo_safety_stock"])),
                    target_stock=Decimal(str(item["demo_target_stock"])),
                    unit_price=Decimal("0"),
                    primary_location_code=item.get("primary_location_code"),
                    lots=tuple(item.get("lots") or []),
                    tags=tuple(
                        dict.fromkeys(
                            [
                                *((item.get("tags") or []) if high_confidence else []),
                                "lcsc-catalog-identity",
                                *(["catalog-unclassified"] if not high_confidence else []),
                            ]
                        )
                    ),
                    attributes=attributes,
                    category_code=CATEGORY_CODE_BY_INFERRED[
                        inferred if high_confidence else "unclassified"
                    ],
                    confidence=confidence,
                    identity_source="lcsc_catalog_identity",
                    notes="",
                )
            )
        return seeds

    def _current_material_maps(self) -> tuple[dict[str, Material], dict[str, list[Material]]]:
        rows = list(
            self.db.scalars(
                select(Material).where(Material.is_deleted.is_(False)).order_by(Material.id)
            ).all()
        )
        by_code = {_fold(item.code): item for item in rows}
        by_mpn: dict[str, list[Material]] = {}
        for item in rows:
            if key := _fold(item.mpn):
                by_mpn.setdefault(key, []).append(item)
        return by_code, by_mpn

    @staticmethod
    def _choose_mpn_match(candidates: list[Material]) -> Material:
        return sorted(candidates, key=lambda item: (item.code.casefold(), item.id))[0]

    def _plan_material_mappings(self, summary: SeedSummary) -> None:
        by_code, by_mpn = self._current_material_maps()
        virtual_codes = set(by_code)
        virtual_mpns = set(by_mpn)
        for seed in self.material_seeds:
            code_key, mpn_key = _fold(seed.code), _fold(seed.mpn)
            if code_key in by_code:
                match = by_code[code_key]
                summary.reused_by_code += 1
                decision = {"kind": "exact_code", "material_code": match.code}
            elif mpn_key and mpn_key in by_mpn:
                candidates = by_mpn[mpn_key]
                match = self._choose_mpn_match(candidates)
                summary.reused_by_mpn += 1
                decision = {"kind": "exact_mpn", "material_code": match.code}
                if len(candidates) > 1:
                    summary.ambiguous_mpn_choices.append(
                        {
                            "source_ref": seed.source_ref,
                            "mpn": seed.mpn,
                            "selected_code": match.code,
                            "candidate_codes": [item.code for item in candidates],
                        }
                    )
            elif code_key in virtual_codes or (mpn_key and mpn_key in virtual_mpns):
                decision = {"kind": "supplied_dataset_dedup", "material_code": seed.code}
                summary.reused_by_mpn += 1
            else:
                summary.created_materials += 1
                decision = {"kind": "create_demo", "material_code": seed.code}
                virtual_codes.add(code_key)
                if mpn_key:
                    virtual_mpns.add(mpn_key)
            summary.material_mappings.append({"source_ref": seed.source_ref, **decision})
            if seed.confidence != "high":
                summary.low_confidence_unclassified.append(
                    {"source_ref": seed.source_ref, "mpn": seed.mpn}
                )

    def _ensure_locations(self, summary: SeedSummary) -> dict[str, Location]:
        root_code = self.v1_locations["root_code"]
        root = self.db.scalar(select(Location).where(Location.code == root_code))
        if root is None:
            raise BusinessError(
                "PORTFOLIO_ROOT_LOCATION_MISSING",
                f"根库位 {root_code} 不存在",
            )
        if root.type != "warehouse":
            raise BusinessError(
                "PORTFOLIO_ROOT_LOCATION_CONFLICT",
                f"根库位 {root_code} 类型冲突",
            )

        by_code = {item.code: item for item in self.db.scalars(select(Location)).all()}
        organizers = [
            *self.v1_locations["organizers"],
            *self.extension_locations["organizers"],
        ]
        for definition in organizers:
            code = definition["code"]
            organizer = by_code.get(code)
            if organizer is None:
                organizer = Location(
                    code=code,
                    name=definition["name"],
                    parent_id=root.id,
                    type="box",
                    full_path=f"{root.full_path} / {definition['name']}",
                    manager=definition.get("manager") or "仓库运营",
                    notes=definition.get("notes") or "",
                    organizer_style=definition["style"],
                    organizer_left_module=definition.get("left_module"),
                    organizer_right_module=definition.get("right_module"),
                )
                self.db.add(organizer)
                self.db.flush()
                by_code[code] = organizer
                summary.created_locations += 1
            elif (
                organizer.parent_id != root.id
                or organizer.type != "box"
                or organizer.organizer_style != definition["style"]
            ):
                raise BusinessError(
                    "PORTFOLIO_LOCATION_CONFLICT",
                    f"库位 {code} 与 Portfolio Demo v2 结构冲突",
                )

            left = definition.get("left_module") or "small"
            right = definition.get("right_module") or "large"
            for slot in organizer_slot_names(definition["style"], left, right):
                child_code = f"{code}-{slot}"
                child = by_code.get(child_code)
                expected_type = "shelf" if definition["style"] == "shelf_rack_6" else "bin"
                if child is None:
                    child_name = organizer_child_name(definition["style"], slot)
                    child = Location(
                        code=child_code,
                        name=child_name,
                        parent_id=organizer.id,
                        type=expected_type,
                        full_path=f"{organizer.full_path} / {child_name}",
                        manager=organizer.manager,
                        notes=organizer_bin_note(definition["style"], slot),
                    )
                    self.db.add(child)
                    self.db.flush()
                    by_code[child_code] = child
                    summary.created_locations += 1
                elif child.parent_id != organizer.id or child.type != expected_type:
                    raise BusinessError(
                        "PORTFOLIO_LOCATION_CONFLICT",
                        f"子库位 {child_code} 与 Portfolio Demo v2 结构冲突",
                    )

            for box_definition in definition.get("boxes") or []:
                box_code = box_definition["code"]
                shelf_code = f"{code}-L{int(box_definition['level']):02d}"
                shelf = by_code[shelf_code]
                box = by_code.get(box_code)
                if box is None:
                    box = Location(
                        code=box_code,
                        name=box_definition["name"],
                        parent_id=shelf.id,
                        type="container",
                        full_path=f"{shelf.full_path} / {box_definition['name']}",
                        manager=organizer.manager,
                        notes="",
                        organizer_style="shelf_storage_box",
                    )
                    self.db.add(box)
                    self.db.flush()
                    by_code[box_code] = box
                    summary.created_locations += 1
                elif box.parent_id != shelf.id or box.type != "container":
                    raise BusinessError(
                        "PORTFOLIO_LOCATION_CONFLICT",
                        f"货架箱 {box_code} 与 Portfolio Demo v2 结构冲突",
                    )
        self.db.commit()
        return by_code

    @staticmethod
    def _is_owned(material: Material, seed: MaterialSeed) -> bool:
        marker = (material.attributes or {}).get("portfolio_demo") or {}
        return (
            marker.get("dataset_version") == DATASET_VERSION
            and marker.get("source_ref") == seed.source_ref
            and marker.get("stock_is_synthetic") is True
        )

    def _enrich_existing(self, material: Material, seed: MaterialSeed) -> bool:
        if seed.identity_source != "lcsc_catalog_identity" or seed.confidence != "high":
            return False
        changed = False
        if not material.mpn and seed.mpn:
            material.mpn = seed.mpn
            changed = True
        if not material.package and seed.package:
            material.package = seed.package
            changed = True
        if not material.supplier_part_number:
            material.supplier_part_number = seed.supplier_part_number
            changed = True
        current_attributes = dict(material.attributes or {})
        for key, value in seed.attributes.items():
            if key == "portfolio_demo":
                continue
            if key not in current_attributes:
                current_attributes[key] = value
                changed = True
        provenance = dict(current_attributes.get("catalog_provenance") or {})
        if not provenance:
            current_attributes["catalog_provenance"] = {
                "identity_source": "lcsc_reconciliation_statement",
                "source_ref": seed.source_ref,
                "confidence": "high",
                "stock_imported": False,
            }
            changed = True
        catalog_tags = [
            tag for tag in seed.tags if tag not in {"portfolio-demo", "internal-engineering"}
        ]
        tags = list(dict.fromkeys([*(material.tags or []), *catalog_tags]))
        if tags != list(material.tags or []):
            material.tags = tags
            changed = True
        if changed:
            material.attributes = current_attributes
            material.updated_by_id = self.operator.id
        return changed

    def _ensure_materials(
        self,
        summary: SeedSummary,
        *,
        location_by_code: dict[str, Location],
        category_by_code: dict[str, Category],
    ) -> tuple[dict[str, Material], list[tuple[MaterialSeed, Material]]]:
        by_code, by_mpn = self._current_material_maps()
        mapping: dict[str, Material] = {}
        owned: list[tuple[MaterialSeed, Material]] = []
        for seed in self.material_seeds:
            code_key, mpn_key = _fold(seed.code), _fold(seed.mpn)
            material = by_code.get(code_key)
            decision = "exact_code"
            candidates: list[Material] = []
            if material is None and mpn_key:
                candidates = by_mpn.get(mpn_key) or []
                if candidates:
                    material = self._choose_mpn_match(candidates)
                    decision = "exact_mpn"
            if material is None:
                location = (
                    location_by_code.get(seed.primary_location_code)
                    if seed.primary_location_code
                    else None
                )
                if seed.primary_location_code and location is None:
                    raise BusinessError(
                        "PORTFOLIO_LOCATION_MISSING",
                        f"物料 {seed.source_ref} 引用的库位不存在",
                        details={"location_code": seed.primary_location_code},
                    )
                material = Material(
                    code=seed.code,
                    name=seed.name,
                    category_id=category_by_code[seed.category_code].id,
                    location_id=location.id if location else None,
                    mpn=seed.mpn,
                    specification=seed.specification,
                    package=seed.package,
                    manufacturer=seed.manufacturer,
                    supplier_part_number=seed.supplier_part_number,
                    unit="pcs",
                    unit_price=seed.unit_price,
                    safety_stock=seed.safety_stock,
                    target_stock=seed.target_stock,
                    quantity=Decimal("0"),
                    reserved_quantity=Decimal("0"),
                    tags=list(seed.tags),
                    attributes=seed.attributes,
                    notes=seed.notes,
                    created_by_id=self.operator.id,
                    updated_by_id=self.operator.id,
                )
                self.db.add(material)
                self.db.flush()
                by_code[code_key] = material
                if mpn_key:
                    by_mpn.setdefault(mpn_key, []).append(material)
                summary.created_materials += 1
                decision = "create_demo"
            elif decision == "exact_code":
                summary.reused_by_code += 1
            else:
                summary.reused_by_mpn += 1
                if len(candidates) > 1:
                    summary.ambiguous_mpn_choices.append(
                        {
                            "source_ref": seed.source_ref,
                            "mpn": seed.mpn,
                            "selected_code": material.code,
                            "candidate_codes": [item.code for item in candidates],
                        }
                    )

            if not self._is_owned(material, seed) and self._enrich_existing(material, seed):
                summary.enriched_existing_materials += 1
            if self._is_owned(material, seed) or decision == "create_demo":
                owned.append((seed, material))
            mapping[seed.source_ref] = material
            summary.material_mappings.append(
                {
                    "source_ref": seed.source_ref,
                    "kind": decision,
                    "material_code": material.code,
                }
            )
            if seed.confidence != "high":
                summary.low_confidence_unclassified.append(
                    {"source_ref": seed.source_ref, "mpn": seed.mpn}
                )
        self.db.commit()
        return mapping, owned

    def _initialize_inventory(
        self,
        summary: SeedSummary,
        inventory: InventoryService,
        owned_seeds: list[tuple[MaterialSeed, Material]],
        locations: dict[str, Location],
    ) -> None:
        for seed, material in owned_seeds:
            stock_result = inventory.inbound(
                material.id,
                seed.quantity,
                _seed_key("stock", seed.source_ref),
                "仓库库存初始化",
                "库存数量由仓库业务记录维护，不来自 LCSC 采购数量。",
                operation_type="initial",
            )
            if not stock_result.get("idempotent_replay"):
                summary.initialized_stock_items += 1
            allocations = [
                {
                    "location_id": locations[item["location_code"]].id,
                    "quantity": Decimal(str(item["quantity"])),
                }
                for item in seed.lots
            ]
            if allocations:
                lot_result = inventory.initialize_location_allocations(
                    material.id,
                    allocations,
                    _seed_key("lots", seed.source_ref),
                    "仓库库位初始化",
                    "库位数量由仓库业务记录维护。",
                )
                if not lot_result.get("idempotent_replay"):
                    summary.initialized_lot_distributions += 1

    @staticmethod
    def _project_owned(project: Project, definition: dict[str, Any]) -> bool:
        return project.code == definition["code"] and project.name == definition["name"]

    def _ensure_projects_and_boms(
        self,
        summary: SeedSummary,
        material_mapping: dict[str, Material],
    ) -> dict[str, Project]:
        project_by_code = {item.code: item for item in self.db.scalars(select(Project)).all()}
        definitions = [
            *self.v1_projects["projects"],
            *self.extension_projects["projects"],
        ]
        result: dict[str, Project] = {}
        for definition in definitions:
            project = project_by_code.get(definition["code"])
            if project is None:
                project = Project(
                    code=definition["code"],
                    name=definition["name"],
                    manager_id=self.operator.id,
                    status=definition["status"],
                    start_date=(
                        date.fromisoformat(definition["start_date"])
                        if definition.get("start_date")
                        else None
                    ),
                    end_date=(
                        date.fromisoformat(definition["end_date"])
                        if definition.get("end_date")
                        else None
                    ),
                    notes=definition.get("notes") or "",
                    members=[self.operator.id],
                )
                self.db.add(project)
                self.db.flush()
                project_by_code[project.code] = project
                summary.created_projects += 1
            elif not self._project_owned(project, definition):
                raise BusinessError(
                    "PORTFOLIO_PROJECT_CODE_CONFLICT",
                    f"项目编码 {project.code} 已被其他项目占用",
                )

            result[project.code] = project
            for version, rows in definition["versions"].items():
                aggregated: dict[int, Decimal] = {}
                for source_ref, quantity in rows:
                    material = material_mapping[source_ref]
                    aggregated[material.id] = aggregated.get(material.id, Decimal("0")) + Decimal(
                        str(quantity)
                    )
                existing = {
                    item.material_id: item
                    for item in self.db.scalars(
                        select(BomItem).where(
                            BomItem.project_id == project.id,
                            BomItem.version == version,
                        )
                    ).all()
                }
                for material_id, required_quantity in aggregated.items():
                    item = existing.get(material_id)
                    if item is None:
                        self.db.add(
                            BomItem(
                                project_id=project.id,
                                version=version,
                                material_id=material_id,
                                required_quantity=required_quantity,
                                notes=(
                                    "required_quantity is total current project demand, "
                                    "not per-unit demand."
                                ),
                            )
                        )
                        summary.created_bom_items += 1
                    elif item.required_quantity != required_quantity:
                        raise BusinessError(
                            "PORTFOLIO_BOM_CONFLICT",
                            "已有 BOM 数量与 v2 数据集不一致，不会自动覆盖",
                            details={
                                "project_code": project.code,
                                "version": version,
                                "material_id": material_id,
                                "existing": str(item.required_quantity),
                                "expected": str(required_quantity),
                            },
                        )
        self.db.commit()
        return result

    def _ensure_reservations(
        self,
        summary: SeedSummary,
        inventory: InventoryService,
        projects: dict[str, Project],
        material_mapping: dict[str, Material],
    ) -> None:
        reservations = [
            *self.v1_projects.get("reservations", []),
            *self.extension_projects.get("reservations", []),
        ]
        for definition in reservations:
            source_ref = definition.get("material_code") or definition.get("material_ref")
            project = projects[definition["project_code"]]
            material = material_mapping[source_ref]
            response = inventory.reserve(
                material.id,
                project.id,
                Decimal(str(definition["quantity"])),
                _seed_key("reserve", project.code, source_ref),
                "项目预留初始化",
                "项目覆盖预留。",
            )
            if not response.get("idempotent_replay"):
                summary.initialized_reservations += 1

    def _database_counts(self) -> dict[str, int]:
        return {
            "materials": int(self.db.scalar(select(func.count()).select_from(Material)) or 0),
            "portfolio_materials": sum(
                self._is_portfolio_material(item)
                for item in self.db.scalars(select(Material)).all()
            ),
            "projects": int(self.db.scalar(select(func.count()).select_from(Project)) or 0),
            "portfolio_projects": sum(
                item.code in {definition["code"] for definition in [
                    *self.v1_projects["projects"],
                    *self.extension_projects["projects"],
                ]}
                for item in self.db.scalars(select(Project)).all()
            ),
            "bom_items": int(self.db.scalar(select(func.count()).select_from(BomItem)) or 0),
            "locations": int(self.db.scalar(select(func.count()).select_from(Location)) or 0),
            "inventory_lots": int(
                self.db.scalar(select(func.count()).select_from(InventoryLot)) or 0
            ),
            "project_reservations": int(
                self.db.scalar(select(func.count()).select_from(ProjectReservation)) or 0
            ),
            "stock_movements": int(
                self.db.scalar(select(func.count()).select_from(StockMovement)) or 0
            ),
        }

    def _invariants(self) -> dict[str, Any]:
        material_failures = int(
            self.db.scalar(
                select(func.count())
                .select_from(Material)
                .where(
                    (Material.quantity < 0)
                    | (Material.reserved_quantity < 0)
                    | (Material.reserved_quantity > Material.quantity)
                )
            )
            or 0
        )
        portfolio_material_ids = [
            item.id
            for item in self.db.scalars(select(Material)).all()
            if self._is_portfolio_material(item)
        ]
        lot_rows = self.db.execute(
            select(
                InventoryLot.material_id,
                func.sum(InventoryLot.quantity),
                func.min(InventoryLot.quantity),
            )
            .where(InventoryLot.material_id.in_(portfolio_material_ids))
            .group_by(InventoryLot.material_id)
        ).all()
        lot_failures = sum(
            minimum < 0 or total > self.db.get(Material, material_id).quantity
            for material_id, total, minimum in lot_rows
        )
        low_confidence_claim_failures = 0
        for material in self.db.scalars(select(Material)).all():
            marker = (material.attributes or {}).get("portfolio_demo") or {}
            if marker.get("catalog_confidence") != "low":
                continue
            technical_keys = set(material.attributes or {}) - {"portfolio_demo"}
            if (
                marker.get("catalog_confidence") != "low"
                or technical_keys
                or material.category_id
                != self.db.scalar(select(Category.id).where(Category.code == "CAT-16"))
            ):
                low_confidence_claim_failures += 1
        return {
            "material_quantity_constraints": material_failures == 0,
            "portfolio_lot_totals_within_material_quantity": lot_failures == 0,
            "low_confidence_items_unclassified": low_confidence_claim_failures == 0,
        }

    @staticmethod
    def _is_portfolio_material(material: Material) -> bool:
        marker = (material.attributes or {}).get("portfolio_demo") or {}
        return marker.get("stock_is_synthetic") is True or (
            material.attributes or {}
        ).get("stock_is_synthetic") is True
