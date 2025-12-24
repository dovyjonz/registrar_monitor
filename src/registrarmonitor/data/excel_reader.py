import datetime
import os
from typing import Tuple

import pandas as pd
import xlrd


class ExcelReader:
    """Reads and processes Excel data files."""

    def read_excel_data(self, input_file: str) -> Tuple[str, str, pd.DataFrame]:
        """Read and process the Excel data file."""
        wb = xlrd.open_workbook(input_file, ignore_workbook_corruption=True)
        sheet = wb.sheet_by_index(0)

        semester = sheet.cell_value(0, 0)
        timestamp = sheet.cell_value(1, 0)

        try:
            # Try to parse with specific format first, then fallback
            datetime_obj = pd.to_datetime(
                timestamp, format="%m/%d/%Y %I:%M:%S %p", errors="raise"
            )
        except ValueError:
            try:
                datetime_obj = pd.to_datetime(timestamp, errors="raise")
            except Exception:  # Broad exception if all parsing fails
                ieee_timestamp = timestamp  # fallback if all conversion fails
            else:
                ieee_timestamp = datetime_obj.strftime("%Y-%m-%d %H:%M:%S")
        else:
            ieee_timestamp = datetime_obj.strftime("%Y-%m-%d %H:%M:%S")

        data = []
        for row_idx in range(
            2, sheet.nrows
        ):  # Headers are usually at row 2 (0-indexed)
            # Assuming headers are in row 2, data starts from row 3
            if row_idx == 2:  # Header row
                header_values = [
                    sheet.cell_value(row_idx, col_idx) for col_idx in range(sheet.ncols)
                ]
            else:  # Data rows
                data.append(
                    [
                        sheet.cell_value(row_idx, col_idx)
                        for col_idx in range(sheet.ncols)
                    ]
                )

        if "header_values" in locals() and data:
            df = pd.DataFrame(data, columns=header_values)
        else:
            raw_data = []
            for r in range(sheet.nrows):
                raw_data.append([sheet.cell_value(r, c) for c in range(sheet.ncols)])
            if len(raw_data) > 3:
                df = pd.DataFrame(raw_data[3:], columns=raw_data[2])
            else:
                return semester, ieee_timestamp, pd.DataFrame()

        s_enr = pd.to_numeric(df["Enr"], errors="coerce")
        df["Enr"] = s_enr.fillna(0).astype(int)  # type: ignore
        s_cap = pd.to_numeric(df["Cap"], errors="coerce")
        df["Cap"] = s_cap.fillna(0).astype(int)  # type: ignore

        df["Fill"] = df.apply(
            lambda row: (row["Enr"] / row["Cap"]) if row["Cap"] > 0 else 0.0, axis=1
        ).round(2)

        # Sanitize instructor names from the 'Faculty' column and rename it
        if "Faculty" in df.columns:
            df["Instructor"] = (
                df["Faculty"]
                .astype(str)
                .str.strip()
                .str.replace(r"\(P\)", "", regex=True)
                .str.strip()
                .replace("", "TBA")
            )
            df.drop(columns=["Faculty"], inplace=True)

        return semester, ieee_timestamp, df

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
