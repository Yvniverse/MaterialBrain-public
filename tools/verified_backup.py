"""Binary-safe Compose backup; publish a manifest only after all archives verify."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import tarfile
from pathlib import Path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree_stats(source: Path) -> dict[str, int | str]:
    files = [path for path in source.rglob("*") if path.is_file()]
    return {
        "source_path": str(source.resolve()),
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
    }


def backup(root: Path, destination: Path, attachments: Path, evidence: Path) -> Path:
    destination, attachments, evidence = (
        destination.resolve(), attachments.resolve(), evidence.resolve()
    )
    for source in (attachments, evidence):
        if not source.is_dir():
            raise ValueError(f"Backup source directory missing: {source}")
    config = json.loads(subprocess.check_output(
        ["docker", "compose", "config", "--format", "json"], cwd=root, text=True,
    ))
    environment = config["services"]["db"]["environment"]
    user, name = environment["POSTGRES_USER"], environment["POSTGRES_DB"]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d_%H%M%S_%f")
    destination.mkdir(parents=True, exist_ok=True)
    dump = destination / f"db_{stamp}.dump"
    with dump.open("xb") as stream:
        subprocess.run(
            ["docker", "compose", "exec", "-T", "db", "pg_dump", "-U", user,
             "-d", name, "-Fc"], cwd=root, stdout=stream, check=True,
        )
    with dump.open("rb") as stream:
        if stream.read(5) != b"PGDMP":
            raise ValueError("Invalid PostgreSQL custom dump header")
    with dump.open("rb") as stream:
        subprocess.run(
            ["docker", "compose", "exec", "-T", "db", "pg_restore", "--list"],
            cwd=root, stdin=stream, stdout=subprocess.DEVNULL, check=True,
        )
    manifest = {
        "timestamp": stamp,
        "database_name": name,
        "database": dump.name,
        "database_sha256": digest(dump),
        "dump_toc_verified": True,
        "compose_project": config.get("name") or os.environ.get("COMPOSE_PROJECT_NAME", ""),
        "backup_directory": str(destination),
    }
    for kind, source in (("attachments", attachments), ("evidence", evidence)):
        archive = destination / f"{kind}_{stamp}.tar.gz"
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(source, arcname=".")
        with tarfile.open(archive, "r:gz") as tar:
            for item in tar:
                if item.isfile():
                    with tar.extractfile(item) as stream:
                        while stream.read(1024 * 1024):
                            pass
        manifest[kind] = archive.name
        manifest[f"{kind}_sha256"] = digest(archive)
        manifest[f"{kind}_source"] = tree_stats(source)
    path = destination / f"backup_manifest_{stamp}.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-directory")
    parser.add_argument("--attachment-directory")
    parser.add_argument("--evidence-directory")
    args = parser.parse_args()
    root = Path(subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], text=True,
    ).strip())
    config = json.loads(subprocess.check_output(
        ["docker", "compose", "config", "--format", "json"], cwd=root, text=True,
    ))
    mounts = {
        mount["target"]: Path(mount["source"])
        for mount in config["services"]["backup"]["volumes"]
        if mount.get("type") == "bind"
    }
    print(
        backup(
            root,
            root / args.backup_directory if args.backup_directory else mounts["/backups"],
            root / args.attachment_directory if args.attachment_directory else mounts["/attachments"],
            root / args.evidence_directory if args.evidence_directory else mounts["/evidence"],
        )
    )


if __name__ == "__main__":
    main()
