import sqlite3
import os
from ..data.excel_reader import ExcelReader
from ..core import get_logger

logger = get_logger(__name__)


def populate_instructors(db_path: str, excel_path: str, dry_run: bool = False) -> bool:
    """
    Populates the instructor field in the sections table from an Excel file.

    Args:
        db_path: The path to the SQLite database.
        excel_path: The path to the Excel file containing instructor data.
        dry_run: If True, simulates the update without committing to the database.

    Returns:
        bool: True if successful (even if 0 updates), False on critical error.
    """
    if not os.path.exists(excel_path):
        logger.error(f"Excel file not found at '{excel_path}'")
        return False

    if not os.path.exists(db_path):
        logger.error(f"Database file not found at '{db_path}'")
        return False

    logger.info(f"Reading instructor data from '{excel_path}'...")
    try:
        reader = ExcelReader()
        _, _, df = reader.read_excel_data(excel_path)
    except Exception as e:
        logger.error(f"Failed to read Excel data: {e}")
        return False

    if df.empty:
        logger.warning("No data found in the Excel file.")
        return True  # Not a failure, just empty

    # The excel_reader renames 'Faculty' to 'Instructor'.
    # We need 'Course Abbr' for the course code and 'S/T' for the section code.
    required_cols = {"Course Abbr", "S/T", "Instructor"}
    if not required_cols.issubset(df.columns):
        logger.error(
            f"Excel file is missing required columns: {required_cols}. Found: {df.columns.tolist()}"
        )
        return False

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        updated_count = 0
        skipped_count = 0
        not_found_count = 0

        logger.info("Processing sections for instructor updates...")
        for _, row in df.iterrows():
            course_code = row["Course Abbr"]
            section_code = row["S/T"]
            instructor = row["Instructor"]

            if not all([isinstance(course_code, str), isinstance(section_code, str)]):
                skipped_count += 1
                continue

            course_code = course_code.strip()
            section_code = section_code.strip()

            # Normalize instructor if None/NaN
            if not isinstance(instructor, str):
                instructor = ""
            instructor = instructor.strip()

            # Find the section_id for the given course and section code
            cursor.execute(
                """
                SELECT s.section_id, s.instructor
                FROM sections s
                JOIN courses c ON s.course_id = c.course_id
                WHERE c.course_code = ? AND s.section_code = ?
                """,
                (course_code, section_code),
            )
            result = cursor.fetchone()

            if result:
                section_id, old_instructor = result
                if old_instructor is None:
                    old_instructor = ""

                if old_instructor != instructor:
                    if not dry_run:
                        cursor.execute(
                            "UPDATE sections SET instructor = ? WHERE section_id = ?",
                            (instructor, section_id),
                        )
                    logger.debug(
                        f"Updating {course_code}-{section_code}: '{old_instructor}' -> '{instructor}'"
                    )
                    updated_count += 1
            else:
                not_found_count += 1

        logger.info(
            f"Instructor Population Summary: Updated={updated_count}, NotFound={not_found_count}, Skipped={skipped_count}"
        )

        if dry_run:
            logger.info("[DRY RUN] No changes were made to the database.")
        else:
            conn.commit()
            logger.info(f"Database '{db_path}' updated successfully.")

        return True

    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        if conn and not dry_run:
            conn.rollback()
        return False
    except Exception as e:
        logger.error(f"Unexpected error during instructor population: {e}")
        if conn and not dry_run:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()
