"""
File I/O Utilities

Handles reading input files and writing output files.
Supports Excel (.xlsx) and CSV (.csv) formats. Also provides streaming helpers
(scan_input, iter_input_chunks, concat_csv_parts) used for large-file, chunked,
resumable processing.
"""

from pathlib import Path
from typing import Iterator, Tuple

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


def scan_input(
    file_path: str,
    id_column: str,
    address_columns: list = None,
    sheet_name: str = None,
) -> dict:
    """
    Inspect an input file without loading the whole thing into memory (for CSV).

    Returns a dictionary with:
        columns          : list of column names
        n_rows           : total number of data rows
        duplicate_ids    : sorted list of up to 10 example duplicate ID values
        n_duplicate_rows : number of rows carrying a duplicated ID

    Used to validate columns, count rows, and enforce unique IDs before
    committing to a large chunked run. For CSV, only the ID column and the
    address column(s) are read, so memory stays bounded even on huge files;
    Excel is read in full (Excel is not a large-scale format anyway).

    The duplicate-ID check is deliberately restricted to rows that will actually
    be geocoded downstream. A row is excluded from the check (and so cannot
    trigger a duplicate error) when it would be rejected by the pipeline, i.e.
    its ID is missing/blank, or every address component is blank. This mirrors
    the pipeline's own rejection rule so a duplicate ID sitting on an otherwise
    unusable row does not abort an otherwise valid file.

    Parameters
    ----------
    file_path       : Path to the input file.
    id_column       : Name of the unique-identifier column.
    address_columns : Column name(s) that make up the address (a single
                      full-address column, or the separate street/city/state/zip
                      columns). Used only to decide which rows would be rejected.
    sheet_name      : Sheet name (Excel only).
    """
    path = Path(file_path)
    suffix = path.suffix.lower()
    address_columns = address_columns or []

    if suffix == ".csv":
        header = pd.read_csv(path, dtype=str, nrows=0)
        columns = list(header.columns)
        if id_column not in columns:
            return {
                "columns": columns,
                "n_rows": 0,
                "duplicate_ids": [],
                "n_duplicate_rows": 0,
            }
        usecols = [id_column] + [
            c for c in address_columns if c and c in columns and c != id_column
        ]
        frame = pd.read_csv(path, dtype=str, usecols=usecols)
    elif suffix in (".xlsx", ".xls"):
        kwargs = {"sheet_name": sheet_name} if sheet_name else {}
        frame = pd.read_excel(path, dtype=str, **kwargs)
        columns = list(frame.columns)
        if id_column not in columns:
            return {
                "columns": columns,
                "n_rows": int(len(frame)),
                "duplicate_ids": [],
                "n_duplicate_rows": 0,
            }
    else:
        raise ValueError(
            f"Unsupported file format: '{path.suffix}'.\n"
            "Please provide a file ending in .xlsx or .csv."
        )

    n_rows = int(len(frame))
    ids = frame[id_column]

    # A row is "present" (eligible for the duplicate check) only if its ID is
    # non-blank. notna() is evaluated on the ORIGINAL series BEFORE astype(str),
    # because astype(str) turns NaN into the literal string "nan".
    id_present = ids.notna() & (ids.astype(str).str.strip() != "")

    present_addr_cols = [c for c in address_columns if c and c in frame.columns]
    if present_addr_cols:
        # Address is blank only when EVERY provided component is blank.
        addr_blank = None
        for c in present_addr_cols:
            col_blank = frame[c].isna() | (frame[c].astype(str).str.strip() == "")
            addr_blank = col_blank if addr_blank is None else (addr_blank & col_blank)
        addr_present = ~addr_blank
    else:
        addr_present = pd.Series(True, index=frame.index)

    eligible = id_present & addr_present
    eligible_ids = ids[eligible].astype(str)
    dup_mask = eligible_ids.duplicated(keep=False)
    return {
        "columns": columns,
        "n_rows": n_rows,
        "duplicate_ids": sorted(eligible_ids[dup_mask].unique())[:10],
        "n_duplicate_rows": int(dup_mask.sum()),
    }


def iter_input_chunks(
    file_path: str, chunk_size: int, sheet_name: str = None
) -> Iterator[Tuple[int, pd.DataFrame]]:
    """
    Yield (chunk_index, DataFrame) blocks of at most `chunk_size` rows.

    CSV is streamed with pandas' native chunked reader so only one chunk is held
    in memory at a time. Excel is read in full and sliced (Excel cannot stream,
    and is not intended for very large inputs).
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".csv":
        reader = pd.read_csv(path, dtype=str, chunksize=chunk_size)
        for idx, chunk in enumerate(reader):
            yield idx, chunk.reset_index(drop=True)
    elif suffix in (".xlsx", ".xls"):
        kwargs = {"sheet_name": sheet_name} if sheet_name else {}
        frame = pd.read_excel(path, dtype=str, **kwargs)
        for idx, start in enumerate(range(0, len(frame), chunk_size)):
            yield idx, frame.iloc[start : start + chunk_size].reset_index(drop=True)
    else:
        raise ValueError(
            f"Unsupported file format: '{path.suffix}'.\n"
            "Please provide a file ending in .xlsx or .csv."
        )


def concat_csv_parts(part_paths: list, output_path: str) -> int:
    """
    Concatenate ordered CSV part files into a single CSV, streaming line by line
    so the full result is never held in memory. Keeps the header from the first
    part only. Returns the number of data rows written.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    header_written = False
    with open(out, "w", encoding="utf-8", newline="") as dst:
        for part in part_paths:
            with open(part, "r", encoding="utf-8", newline="") as src:
                for j, line in enumerate(src):
                    if j == 0:
                        # keep the header from the first part only
                        if not header_written:
                            dst.write(line)
                            header_written = True
                        continue
                    dst.write(line)
                    rows += 1
    print(f"Output saved to: {out}")
    return rows
