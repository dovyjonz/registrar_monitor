#!/usr/bin/env python3
"""Build a categorized, checksummed view of collected registrar archives."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import xlrd

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "data" / "registrar_archive"
SOURCES = ARCHIVE / "sources"
ORGANIZED = ARCHIVE / "organized"
MANIFESTS = ARCHIVE / "manifests"


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return cleaned or "unclassified"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def xls_metadata(path: Path) -> tuple[str, str]:
    workbook = xlrd.open_workbook(str(path), ignore_workbook_corruption=True)
    sheet = workbook.sheet_by_index(0)
    semester = str(sheet.cell_value(0, 0)).strip() or "Unclassified"
    timestamp = str(sheet.cell_value(1, 0)).strip() if sheet.nrows > 1 else ""
    return semester, timestamp


def db_metadata(path: Path) -> tuple[str, int, str, str]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if "snapshots" not in tables:
            return "Unclassified", 0, "", ""
        row = connection.execute(
            "SELECT COALESCE(group_concat(DISTINCT semester), ''), "
            "COUNT(*), COALESCE(MIN(timestamp), ''), "
            "COALESCE(MAX(timestamp), '') FROM snapshots"
        ).fetchone()
        if row is None:
            raise sqlite3.DatabaseError(f"metadata query returned no row for {path}")
        semester = str(row[0]).strip() or "Unclassified"
        return semester, int(row[1]), str(row[2]), str(row[3])
    finally:
        connection.close()


def json_metadata(path: Path) -> tuple[str, str]:
    match = re.match(
        r"(?P<semester>[a-z]+_\d{4})_(?P<date>\d{4}-\d{2}-\d{2})_"
        r"(?P<time>\d{2}-\d{2}-\d{2})\.json$",
        path.name,
    )
    if not match:
        return "Unclassified", ""
    semester = match.group("semester").replace("_", " ").title()
    timestamp = f"{match.group('date')} {match.group('time').replace('-', ':')}"
    return semester, timestamp


def source_name(path: Path) -> str:
    relative = path.relative_to(SOURCES)
    return relative.parts[0]


def unique_target(base: Path, relative: Path, digest: str) -> Path:
    target = base / relative
    if not target.exists():
        return target
    if sha256(target) == digest:
        return target
    return target.with_name(f"{target.stem}__{digest[:12]}{target.suffix}")


def link_view(source: Path, semester: str, kind: str, digest: str) -> Path:
    source_root = SOURCES / source_name(source)
    relative = source.relative_to(source_root)
    base = ORGANIZED / slug(semester) / kind / source_name(source)
    target = unique_target(base, relative, digest)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        os.link(source, target)
    return target


def main() -> None:
    ORGANIZED.mkdir(parents=True, exist_ok=True)
    MANIFESTS.mkdir(parents=True, exist_ok=True)

    xls_rows: list[dict[str, object]] = []
    json_rows: list[dict[str, object]] = []
    db_rows: list[dict[str, object]] = []
    hashes: defaultdict[str, list[str]] = defaultdict(list)

    for path in sorted(SOURCES.rglob("*.xls")):
        semester, timestamp = xls_metadata(path)
        digest = sha256(path)
        view = link_view(path, semester, "xls", digest)
        relative = str(path.relative_to(ARCHIVE))
        hashes[digest].append(relative)
        xls_rows.append(
            {
                "source": source_name(path),
                "source_path": relative,
                "organized_path": str(view.relative_to(ARCHIVE)),
                "semester": semester,
                "timestamp": timestamp,
                "bytes": path.stat().st_size,
                "sha256": digest,
            }
        )

    for path in sorted(SOURCES.rglob("*.json")):
        semester, timestamp = json_metadata(path)
        digest = sha256(path)
        view = link_view(path, semester, "json", digest)
        relative = str(path.relative_to(ARCHIVE))
        hashes[digest].append(relative)
        json_rows.append(
            {
                "source": source_name(path),
                "source_path": relative,
                "organized_path": str(view.relative_to(ARCHIVE)),
                "semester": semester,
                "timestamp": timestamp,
                "bytes": path.stat().st_size,
                "sha256": digest,
            }
        )

    for path in sorted(SOURCES.rglob("*.db")):
        semester, snapshots, earliest, latest = db_metadata(path)
        digest = sha256(path)
        view = link_view(path, semester, "databases", digest)
        relative = str(path.relative_to(ARCHIVE))
        hashes[digest].append(relative)
        db_rows.append(
            {
                "source": source_name(path),
                "source_path": relative,
                "organized_path": str(view.relative_to(ARCHIVE)),
                "semester": semester,
                "snapshots": snapshots,
                "earliest_timestamp": earliest,
                "latest_timestamp": latest,
                "bytes": path.stat().st_size,
                "sha256": digest,
            }
        )

    with (MANIFESTS / "xls_inventory.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(xls_rows[0]))
        writer.writeheader()
        writer.writerows(xls_rows)

    with (MANIFESTS / "json_inventory.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=list(json_rows[0]))
        writer.writeheader()
        writer.writerows(json_rows)

    with (MANIFESTS / "database_inventory.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=list(db_rows[0]))
        writer.writeheader()
        writer.writerows(db_rows)

    duplicate_rows = [
        {"sha256": digest, "instances": len(paths), "paths": " | ".join(paths)}
        for digest, paths in sorted(hashes.items())
        if len(paths) > 1
    ]
    with (MANIFESTS / "duplicate_groups.csv").open(
        "w", newline="", encoding="utf-8"
    ) as f:
        writer = csv.DictWriter(f, fieldnames=["sha256", "instances", "paths"])
        writer.writeheader()
        writer.writerows(duplicate_rows)

    summary = {
        "xls_instances": len(xls_rows),
        "xls_unique_sha256": len({str(row["sha256"]) for row in xls_rows}),
        "json_instances": len(json_rows),
        "json_unique_sha256": len({str(row["sha256"]) for row in json_rows}),
        "database_instances": len(db_rows),
        "database_unique_sha256": len({str(row["sha256"]) for row in db_rows}),
        "duplicate_hash_groups": len(duplicate_rows),
        "xls_by_semester": dict(
            sorted(Counter(str(row["semester"]) for row in xls_rows).items())
        ),
        "databases_by_semester": dict(
            sorted(Counter(str(row["semester"]) for row in db_rows).items())
        ),
        "json_by_semester": dict(
            sorted(Counter(str(row["semester"]) for row in json_rows).items())
        ),
    }
    (MANIFESTS / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
