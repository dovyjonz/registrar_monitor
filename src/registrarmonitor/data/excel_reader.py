import datetime
import os
import re
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Tuple, List, Dict, Any

import xlrd


class ExcelReader:
    """Reads and processes Excel data files."""

    def read_excel_data(self, input_file: str) -> Tuple[str, str, List[Dict[str, Any]]]:
        """Read and process the Excel data file."""
        wb = xlrd.open_workbook(input_file, ignore_workbook_corruption=True)
        sheet = wb.sheet_by_index(0)

        # Semester and timestamp are expected in the first two rows
        semester = str(sheet.cell_value(0, 0))
        timestamp = str(sheet.cell_value(1, 0))
        ieee_timestamp = timestamp

        # Timestamp parsing
        parsed = False
        try:
            # Try to parse with specific format first
            datetime_obj = datetime.datetime.strptime(timestamp, "%m/%d/%Y %I:%M:%S %p")
            ieee_timestamp = datetime_obj.strftime("%Y-%m-%d %H:%M:%S")
            parsed = True
        except ValueError:
            pass

        if not parsed:
            # Fallback attempts for common formats
            for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"]:
                try:
                    datetime_obj = datetime.datetime.strptime(timestamp, fmt)
                    ieee_timestamp = datetime_obj.strftime("%Y-%m-%d %H:%M:%S")
                    parsed = True
                    break
                except ValueError:
                    continue

        # Extract data
        header_values: List[str] = []
        raw_rows: List[List[Any]] = []

        # Standard case: headers at row 2
        if sheet.nrows > 2:
            header_values = [str(sheet.cell_value(2, col_idx)) for col_idx in range(sheet.ncols)]
            for row_idx in range(3, sheet.nrows):
                raw_rows.append([sheet.cell_value(row_idx, col_idx) for col_idx in range(sheet.ncols)])

        if not raw_rows and sheet.nrows > 3:
             # Fallback case (replicating original logic structure)
             header_values = [str(sheet.cell_value(2, col_idx)) for col_idx in range(sheet.ncols)]
             for row_idx in range(3, sheet.nrows):
                raw_rows.append([sheet.cell_value(row_idx, col_idx) for col_idx in range(sheet.ncols)])

        if not raw_rows:
            return semester, ieee_timestamp, []

        processed_data: List[Dict[str, Any]] = []

        for row in raw_rows:
            # Create dict based on headers
            record: Dict[str, Any] = {}
            for i, header in enumerate(header_values):
                if i < len(row):
                    record[header] = row[i]
                else:
                    record[header] = ""

            # Enr processing
            try:
                val = record.get("Enr", 0)
                if val == "": val = 0
                enr = int(float(val))
            except (ValueError, TypeError):
                enr = 0
            record["Enr"] = enr

            # Cap processing
            try:
                val = record.get("Cap", 0)
                if val == "": val = 0
                cap = int(float(val))
            except (ValueError, TypeError):
                cap = 0
            record["Cap"] = cap

            # Fill processing
            if cap > 0:
                # Use Decimal for precision matching pandas/numpy "round half to even"
                fill = float(
                    (Decimal(enr) / Decimal(cap))
                    .quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
                )
                record["Fill"] = fill
            else:
                record["Fill"] = 0.0

            # Faculty processing
            if "Faculty" in record:
                faculty = str(record["Faculty"]).strip()
                # Remove (P) using regex
                faculty = re.sub(r"\(P\)", "", faculty)
                faculty = faculty.strip()
                if not faculty:
                    faculty = "TBA"
                record["Instructor"] = faculty
                del record["Faculty"]

            processed_data.append(record)

        return semester, ieee_timestamp, processed_data

    def get_timestamp_from_file(self, file_path: str) -> str:
        """
        Get the timestamp from the file's modification time.

        Args:
            file_path: The path to the file.

        Returns:
            A formatted timestamp string.
        """
        try:
            mtime = os.path.getmtime(file_path)
            dt_object = datetime.datetime.fromtimestamp(mtime)
            return dt_object.strftime("%Y-%m-%d %H:%M:%S")
        except FileNotFoundError:
            # Fallback to current time if file not found, though unlikely
            return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
