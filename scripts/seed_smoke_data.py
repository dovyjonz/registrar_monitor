"""Create a deterministic generated-site fixture for browser and crawl CI jobs."""

from datetime import datetime, timedelta
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


_SNAPSHOT_TIMESTAMPS = {
    "Fall 2026": [
        "2026-08-05 08:55:00",
        "2026-08-05 09:05:00",
        "2026-08-13 09:05:00",
        "2026-08-14 09:05:00",
        "2026-08-20 12:00:00",
    ],
    "Summer 2026": [
        "2026-05-12 09:55:00",
        "2026-05-12 10:05:00",
        "2026-05-13 10:05:00",
        "2026-05-14 10:05:00",
        "2026-06-05 17:30:00",
    ],
    "Spring 2026": [
        "2025-12-17 08:55:00",
        "2025-12-17 09:05:00",
        "2025-12-18 09:05:00",
        "2025-12-19 09:05:00",
    ],
    "Fall 2025": [
        (datetime(2025, 8, 6, 8, 55) + timedelta(minutes=45 * step)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        for step in range(292)
    ],
    "Summer 2025": [
        "2025-05-12 09:55:00",
        "2025-05-12 10:05:00",
        "2025-05-13 10:05:00",
        "2025-05-14 10:05:00",
    ],
}


def _section(
    section_id: str,
    section_type: str,
    enrollment: int,
    *,
    capacity: int = 20,
    instructor: str = "Test Instructor",
) -> Section:
    return Section(
        section_id,
        section_type,
        enrollment,
        capacity,
        enrollment / capacity,
        instructor,
    )


def _course(
    code: str,
    sections: dict[str, Section],
    title: str,
) -> Course:
    average_fill = sum(section.fill for section in sections.values()) / len(sections)
    return Course(code, code.split()[0], sections, average_fill, title)


def _common_courses(semester: str, step: int) -> dict[str, Course]:
    enrollment = 8 + (step % 11)
    return {
        "MATH 161": _course(
            "MATH 161",
            {
                "1L": _section("1L", "Lecture", enrollment),
                "1R": _section("1R", "Recitation", max(6, enrollment - 2)),
                "3L": _section("3L", "Lecture", max(5, enrollment - 4)),
            },
            "Calculus I",
        ),
        "KAZ 368": _course(
            "KAZ 368",
            {"001": _section("001", "Lecture", enrollment)},
            "Onomastics: History and Function of Names",
        ),
        "KAZ 100": _course(
            "KAZ 100",
            {"001": _section("001", "Lecture", enrollment)},
            f"Semester identity fixture ({semester})",
        ),
    }


def _courses_for_semester(semester: str, step: int) -> dict[str, Course]:
    courses = _common_courses(semester, step)
    enrollment = 8 + (step % 11)

    if semester == "Fall 2026":
        courses.update(
            {
                "ANT 101": _course(
                    "ANT 101",
                    {"001": _section("001", "Lecture", min(enrollment, 12))},
                    "Introduction to Anthropology",
                ),
                "ANT 233": _course(
                    "ANT 233",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Current-term-only history fixture",
                ),
                "BIOL 101": _course(
                    "BIOL 101",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Introduction to Biology",
                ),
                "HST 100": _course(
                    "HST 100",
                    {"001": _section("001", "Lecture", enrollment)},
                    "World History",
                ),
                "HST 104": _course(
                    "HST 104",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Modern History",
                ),
                "LING 131": _course(
                    "LING 131",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Introduction to Linguistics",
                ),
                "PHYS 101": _course(
                    "PHYS 101",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Physics fixture",
                ),
                "SOC 101": _course(
                    "SOC 101",
                    {"001": _section("001", "Lecture", 18)},
                    "Sociology fixture",
                ),
                "TEST 101": _course(
                    "TEST 101",
                    {"001": _section("001", "Lecture", 20)},
                    "Generated-site smoke fixture",
                ),
                "WLL 101": _course(
                    "WLL 101",
                    {"001": _section("001", "Lecture", enrollment)},
                    "World languages fixture",
                ),
                "ZOO 101": _course(
                    "ZOO 101",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Scroll-tail fixture",
                ),
                "ZOO 102": _course(
                    "ZOO 102",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Scroll-tail fixture",
                ),
                "ZOO 103": _course(
                    "ZOO 103",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Scroll-tail fixture",
                ),
                "ZOO 104": _course(
                    "ZOO 104",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Scroll-tail fixture",
                ),
                "ZOO 105": _course(
                    "ZOO 105",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Scroll-tail fixture",
                ),
                "ZOO 106": _course(
                    "ZOO 106",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Scroll-tail fixture",
                ),
                "ZOO 107": _course(
                    "ZOO 107",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Scroll-tail fixture",
                ),
                "ZOO 108": _course(
                    "ZOO 108",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Scroll-tail fixture",
                ),
                "ZOO 109": _course(
                    "ZOO 109",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Scroll-tail fixture",
                ),
                "ZOO 110": _course(
                    "ZOO 110",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Scroll-tail fixture",
                ),
                "ZOO 111": _course(
                    "ZOO 111",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Scroll-tail fixture",
                ),
                "ZOO 112": _course(
                    "ZOO 112",
                    {"001": _section("001", "Lecture", enrollment)},
                    "Scroll-tail fixture",
                ),
            }
        )

    if semester == "Summer 2026":
        courses.update(
            {
                "ANT 110": _course(
                    "ANT 110",
                    {"001": _section("001", "Lecture", 20)},
                    "Ordinary full-course fixture",
                ),
                "ANT 111": _course(
                    "ANT 111",
                    {"001": _section("001", "Lecture", 25)},
                    "Over-capacity course fixture",
                ),
                "BUS 101": _course(
                    "BUS 101",
                    {"001": _section("001", "Lecture", 20)},
                    "Full-capacity graph fixture",
                ),
                "CHME 403": _course(
                    "CHME 403",
                    {
                        "1L": _section("1L", "Lecture", 20),
                        "1B": _section("1B", "Lab", 20),
                    },
                    "Required-type-full fixture",
                ),
            }
        )

    return courses


def main(
    *,
    data_dir: Path | None = None,
    report_dir: Path | None = None,
) -> None:
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
        configured_mode, metadata_mode = _configured_storage(semester)
        timestamps = _SNAPSHOT_TIMESTAMPS.get(semester)
        if timestamps is None:
            raise ValueError(f"no smoke-fixture timestamps for {semester!r}")
        for step, timestamp in enumerate(timestamps):
            courses = _courses_for_semester(semester, step)
            snapshot = EnrollmentSnapshot(
                timestamp=timestamp,
                semester=semester,
                overall_fill=sum(course.average_fill for course in courses.values())
                / len(courses),
                courses=courses,
            )
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
