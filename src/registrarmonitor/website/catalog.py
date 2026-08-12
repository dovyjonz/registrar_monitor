"""Storage-backed publication catalog for configured semesters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from registrarmonitor.data.database_manager import DatabaseManager

from .config import get_configured_semesters, get_registrar_url


class CatalogDatabase(Protocol):
    def get_latest_snapshot_id(self) -> int | None: ...

    def get_snapshot_data(self, snapshot_id: int) -> Any: ...


@dataclass(frozen=True)
class PublishedSemester:
    label: str
    registrar_url: str
    database: CatalogDatabase


DatabaseFactory = Callable[[str], Any]


def _existing_database_labels() -> set[str]:
    return set(DatabaseManager.get_semester_databases())


def build_publication_catalog(
    *,
    database_factory: DatabaseFactory | None = None,
    existing_labels: set[str] | None = None,
) -> list[PublishedSemester]:
    """Return newest-first semesters whose latest stored snapshot is publishable."""
    labels = (
        existing_labels if existing_labels is not None else _existing_database_labels()
    )
    factory = database_factory or (lambda semester: DatabaseManager(semester=semester))
    catalog: list[PublishedSemester] = []
    for semester in get_configured_semesters():
        if semester not in labels:
            continue
        try:
            database = factory(semester)
            snapshot_id = database.get_latest_snapshot_id()
            snapshot = database.get_snapshot_data(snapshot_id) if snapshot_id else None
        except Exception:
            continue
        if snapshot is None or snapshot.semester != semester or not snapshot.courses:
            continue
        catalog.append(
            PublishedSemester(
                label=semester,
                registrar_url=get_registrar_url(semester),
                database=database,
            )
        )
    return catalog
