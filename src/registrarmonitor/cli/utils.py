from typing import Optional

from ..data.database_manager import DatabaseManager


async def detect_active_semester(debug: bool = False) -> Optional[str]:
    """Detect which semester database has the most recent data."""
    try:
        available_semesters = DatabaseManager.get_semester_databases()
        if not available_semesters:
            return None

        latest_semester = None
        latest_timestamp = None

        for semester, db_path in available_semesters.items():
            try:
                db = DatabaseManager.create_for_semester(semester)
                latest_id = db.get_latest_snapshot_id()
                if latest_id:
                    # Get the timestamp of the latest snapshot
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "SELECT timestamp FROM snapshots WHERE snapshot_id = ?",
                            (latest_id,),
                        )
                        result = cursor.fetchone()
                        if result and (
                            latest_timestamp is None or result[0] > latest_timestamp
                        ):
                            latest_timestamp = result[0]
                            latest_semester = semester
            except Exception:
                continue

        if latest_semester and debug:
            print(f"🔍 DEBUG: Detected active semester: {latest_semester}")

        return latest_semester
    except Exception as e:
        if debug:
            print(f"🔍 DEBUG: Semester detection failed: {e}")
        return None
