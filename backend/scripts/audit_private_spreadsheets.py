"""Read-only privacy-safe audit for local transformation spreadsheets.

The script intentionally prints neither filenames, cell values, order identities nor URLs.
It exists to prove that repository parse_upload() can inspect every private XLS/XLSX input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.v1.files import parse_upload


def _shape(rows: list[dict]) -> str:
    headers = {str(key).strip().casefold() for row in rows[:5] for key in row}
    if (
        len(
            headers.intersection(
                {"comment", "description", "designator", "footprint", "libref", "quantity"}
            )
        )
        >= 2
    ):
        return "bom_structure"
    if any(value in headers for value in ("线缆名称", "接头间距", "pin数", "长度")):
        return "cable_catalog"
    if any(value in headers for value in ("code", "物料编码", "商品编号", "商品型号")):
        return "material_catalog"
    return "unclassified_table"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    files = sorted(
        path
        for path in args.directory.rglob("*")
        if path.is_file() and path.suffix.casefold() in {".xls", ".xlsx"}
    )
    results = []
    for index, path in enumerate(files, 1):
        try:
            rows = parse_upload(path.read_bytes(), path.name)
            results.append(
                {
                    "source_index": index,
                    "extension": path.suffix.casefold(),
                    "row_count": len(rows),
                    "shape": _shape(rows),
                    "parsed": True,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "source_index": index,
                    "extension": path.suffix.casefold(),
                    "row_count": 0,
                    "shape": "parse_error",
                    "parsed": False,
                    "error_type": type(exc).__name__,
                }
            )
    print(
        json.dumps(
            {
                "file_count": len(files),
                "parsed_count": sum(item["parsed"] for item in results),
                "private_values_printed": False,
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if all(item["parsed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
