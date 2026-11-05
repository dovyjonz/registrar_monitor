import logging

from ..data.database_manager import DatabaseManager

logger = logging.getLogger(__name__)


async def detect_active_semester(debug: bool = False) -> str | None:
    """Detect which semester database has the most recent data."""
    return find_active_semester(debug)


def find_active_semester(debug: bool = False) -> str | None:
    """Synchronously detect the semester with the freshest stored snapshot."""
    try:
        available_semesters = DatabaseManager.get_semester_databases()
        if not available_semesters:
            return None

        latest_semester = None
        latest_timestamp = None

        for semester, db_path in available_semesters.items():
            try:
                db = DatabaseManager.create_for_semester(semester)
                timestamp = db.get_latest_snapshot_last_seen_at()
                if timestamp and (
                    latest_timestamp is None or timestamp > latest_timestamp
                ):
                    latest_timestamp = timestamp
                    latest_semester = semester
            except Exception:
                continue

        if latest_semester:
            logger.debug("Detected active semester=%s", latest_semester)

        return latest_semester
    except Exception as e:
        logger.debug("Semester detection failed: %s", e)
        return None
