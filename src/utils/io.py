"""
File I/O Utilities

Handles reading input files and writing output files.
Supports Excel (.xlsx) and CSV (.csv) formats.
"""

from pathlib import Path

import pandas as pd


def read_input(file_path: str, sheet_name: str = None) -> pd.DataFrame:
    """
    Read an Excel or CSV file into a DataFrame.

    Parameters
    ----------
    file_path  : Path to the input file.
    sheet_name : Sheet name (Excel only). If None, reads the first sheet.

    Returns
    -------
    DataFrame with all columns read as strings to preserve formatting.
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix in (".xlsx", ".xls"):
        kwargs = {"sheet_name": sheet_name} if sheet_name else {}
        return pd.read_excel(path, dtype=str, **kwargs)
    elif suffix == ".csv":
        return pd.read_csv(path, dtype=str)
    else:
        raise ValueError(
            f"Unsupported file format: '{path.suffix}'.\n"
            "Please provide a file ending in .xlsx or .csv."
        )


def write_output(df: pd.DataFrame, file_path: str) -> None:
    """
    Write a DataFrame to an Excel or CSV file.

    Parameters
    ----------
    df        : DataFrame to write.
    file_path : Path for the output file (.xlsx or .csv).
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()

    if suffix in (".xlsx", ".xls"):
        df.to_excel(path, index=False)
    elif suffix == ".csv":
        df.to_csv(path, index=False)
    else:
        raise ValueError(
            f"Unsupported output format: '{path.suffix}'.\n"
            "Please use a file ending in .xlsx or .csv."
        )

    print(f"Output saved to: {path}")
