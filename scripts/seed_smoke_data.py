"""Create a tiny generated-site fixture for browser and crawl CI jobs."""

from pathlib import Path

from registrarmonitor.config import get_config
from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.data.migration import (
    MetadataMode,
    finalize_storage,
    initialize_fresh_storage,
    transition_storage_mode,
)
from registrarmonitor.models import Course, EnrollmentSnapshot, Section
from registrarmonitor.website.config import get_configured_semesters


def _configured_storage(semester: str) -> tuple[str, MetadataMode]:
    config = get_config()
    storage = config.get("storage", {})
    semester_configs = storage.get("semesters", {}) if isinstance(storage, dict) else {}
    semester_config = (
        semester_configs.get(semester) if isinstance(semester_configs, dict) else None
    )
    if not isinstance(semester_config, dict):
        raise TypeError(f"no storage rollout configuration for {semester!r}")

    mode = semester_config.get("mode", "legacy")
    metadata_mode = semester_config.get(
        "metadata_mode", MetadataMode.LEGACY_PRESERVING.value
    )
    if not isinstance(mode, str) or not isinstance(metadata_mode, str):
        raise TypeError(f"invalid storage rollout configuration for {semester!r}")
    return mode, MetadataMode(metadata_mode)


def _seed_semester(
    *,
    semester: str,
    snapshot: EnrollmentSnapshot,
    data_dir: Path,
    report_dir: Path,
    configured_mode: str,
    metadata_mode: MetadataMode,
) -> DatabaseManager:
    """Seed one database while preserving its configured storage contract."""
    if configured_mode not in {"legacy", "shadow", "v2", "finalized"}:
        raise ValueError(f"unsupported configured storage mode {configured_mode!r}")

    database = data_dir / (
        f"enrollment_{DatabaseManager._sanitize_semester_name_static(semester)}.db"
    )
    report_dir.mkdir(parents=True, exist_ok=True)

    if database.exists():
        existing = DatabaseManager(db_path=str(database), semester=semester)
        if existing.storage_mode == configured_mode:
            existing.store_enrollment_snapshot(snapshot)
            return existing
        if (
            existing.storage_mode != "legacy"
            or existing.get_latest_snapshot_id() is not None
        ):
            raise RuntimeError(
                f"existing {semester!r} database does not match configured "
                f"storage mode {configured_mode!r}"
            )

    if configured_mode == "legacy":
        manager = DatabaseManager(db_path=str(database), semester=semester)
        manager.store_enrollment_snapshot(snapshot)
        return manager

    slug = DatabaseManager._sanitize_semester_name_static(semester)
    initialize_fresh_storage(
        database,
        semester=semester,
        metadata_mode=metadata_mode,
        report_path=report_dir / f"{slug}-initialize.json",
    )
    manager = DatabaseManager(db_path=str(database), semester=semester)
    manager.store_enrollment_snapshot(snapshot)

    if configured_mode == "shadow":
        return manager

    transition_storage_mode(
        database,
        semester=semester,
        target_mode="v2",
        report_path=report_dir / f"{slug}-v2.json",
    )
    if configured_mode == "finalized":
        finalize_storage(
            database,
            semester=semester,
            report_path=report_dir / f"{slug}-finalize.json",
            rollback_dir=report_dir / "rollback",
            authorized=True,
        )
    return DatabaseManager(db_path=str(database), semester=semester)


def main(
    *,
    data_dir: Path | None = None,
    report_dir: Path | None = None,
) -> None:
    test_section = Section("001", "Lecture", 12, 20, 0.6, "Test Instructor")
    test_course = Course(
        "TEST 101",
        "TEST",
        {"001": test_section},
        0.6,
        "Generated-site smoke fixture",
    )
    math_sections = {
        "1L": Section("1L", "Lecture", 12, 20, 0.6, "Test Instructor"),
        "1R": Section("1R", "Recitation", 8, 20, 0.4, "Test Instructor"),
    }
    math_course = Course(
        "MATH 161",
        "MATH",
        math_sections,
        0.5,
        "Generated historical-comparison fixture",
    )
    kaz_course = Course(
        "KAZ 368",
        "KAZ",
        {"001": Section("001", "Lecture", 12, 20, 0.6, "Test Instructor")},
        0.6,
        "Onomastics: History and Function of Names",
    )
    config = get_config()
    if data_dir is None:
        directories = config.get("directories", {})
        if not isinstance(directories, dict) or not isinstance(
            directories.get("data_storage"), str
        ):
            raise ValueError("settings.toml must define directories.data_storage")
        data_dir = Path(directories["data_storage"])
    if report_dir is None:
        report_dir = Path("output/generated-site-smoke")

    for semester in get_configured_semesters():
        snapshot = EnrollmentSnapshot(
            timestamp="2026-07-29 00:00:00",
            semester=semester,
            overall_fill=0.6,
            courses={
                "TEST 101": test_course,
                "MATH 161": math_course,
                "KAZ 368": kaz_course,
            },
        )
        configured_mode, metadata_mode = _configured_storage(semester)
        _seed_semester(
            semester=semester,
            snapshot=snapshot,
            data_dir=data_dir,
            report_dir=report_dir,
            configured_mode=configured_mode,
            metadata_mode=metadata_mode,
        )


if __name__ == "__main__":
    main()
