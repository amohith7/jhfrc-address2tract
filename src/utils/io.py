"""
File I/O Utilities

Handles reading input files and writing output files.
Supports Excel (.xlsx) and CSV (.csv) formats. Also provides streaming helpers
(scan_input, iter_input_chunks, concat_csv_parts) used for large-file, chunked,
resumable processing.
"""

import re
from pathlib import Path
from typing import Iterator, Tuple

import pandas as pd

# Matches a whole number that Excel stored as a float, e.g. "30720.0" or the ZIP
# and ID codes it silently turns into floats. Used to restore the clean integer.
_INT_DOT_ZERO = re.compile(r"^(-?\d+)\.0$")


def normalize_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return the frame with every column as uniform text.

    Trims surrounding whitespace and removes the trailing ".0" that Excel adds
    when it reads a whole number (so "30720.0" becomes "30720"), giving each
    column a single, consistent text format. Blank cells stay NaN so the
    downstream missing-value checks are unaffected.
    """
    df = df.copy()
    for col in df.columns:
        s = df[col]
        if s.dtype == object or str(s.dtype) == "string":
            s = s.str.strip()
            s = s.str.replace(_INT_DOT_ZERO, r"\1", regex=True)
            df[col] = s
    return df


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
        return normalize_text_columns(pd.read_excel(path, dtype=str, **kwargs))
    elif suffix == ".csv":
        return normalize_text_columns(pd.read_csv(path, dtype=str))
    else:
        raise ValueError(
            f"Unsupported file format: '{path.suffix}'.\n"
            "Please provide a file ending in .xlsx or .csv."
        )


# Columns that are lookup keys (for Excel XLOOKUP/VLOOKUP). They are written as
# uniform text so every value has the same type: leading zeros are preserved and
# long codes never render in scientific notation, so a lookup matches reliably.
KEY_TEXT_COLUMNS = ("census_tract_geoid",)


def _as_clean_text(value) -> str:
    """Render a single value as a stable text key.

    NaN/None becomes an empty string (never the literal "nan"), and a value that
    arrived as a whole-number float (e.g. 47065.0) drops the trailing ".0" so it
    matches the same code stored as text elsewhere.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if pd.isna(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def write_output(df: pd.DataFrame, file_path: str, text_columns=None) -> None:
    """
    Write a DataFrame to an Excel or CSV file.

    Parameters
    ----------
    df           : DataFrame to write.
    file_path    : Path for the output file (.xlsx or .csv).
    text_columns : Columns to force to a uniform text format so Excel lookup
                   functions (XLOOKUP/VLOOKUP) match reliably. Defaults to the
                   key columns present in the frame.
    """
    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()

    if text_columns is None:
        text_columns = [c for c in KEY_TEXT_COLUMNS if c in df.columns]
    else:
        text_columns = [c for c in text_columns if c in df.columns]

    df = df.copy()
    for col in text_columns:
        df[col] = df[col].map(_as_clean_text)

    if suffix in (".xlsx", ".xls"):
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            df.to_excel(writer, index=False)
            ws = writer.sheets[list(writer.sheets)[0]]
            # Force Text number format ('@') on the key columns so Excel keeps
            # them as text and a lookup against text keys matches.
            for col in text_columns:
                cidx = df.columns.get_loc(col) + 1  # 1-based, no index column
                for row in range(2, ws.max_row + 1):  # skip the header row
                    ws.cell(row=row, column=cidx).number_format = "@"
    elif suffix == ".csv":
        df.to_csv(path, index=False)
    else:
        raise ValueError(
            f"Unsupported output format: '{path.suffix}'.\n"
            "Please use a file ending in .xlsx or .csv."
        )

    print(f"Output saved to: {path}")


# Columns kept in the de-identified file that is shared with JHFRC. It carries
# no street address and no free-text fields, so it contains no PHI.
SHARE_TAIL_COLUMNS = ("census_tract_geoid", "match_status")


def share_file_path(output_path: str) -> Path:
    """Companion path for the de-identified share file (``*_to_share``)."""
    p = Path(output_path)
    return p.with_name(p.stem + "_to_share" + p.suffix)


def write_share_file(df: pd.DataFrame, output_path: str, id_column: str) -> Path:
    """
    Write the de-identified copy to share with JHFRC.

    Keeps only the ID, the Census tract, and the match status. There is no
    street address and no free-text column, so the file carries no PHI. The full
    output (which keeps the address for the partner's own verification) stays on
    the partner's machine.

    Returns the path written.
    """
    keep = [id_column] + [c for c in SHARE_TAIL_COLUMNS if c in df.columns]
    keep = [c for c in keep if c in df.columns]
    share_df = df[keep]
    out = share_file_path(output_path)
    text_cols = [c for c in (id_column, "census_tract_geoid") if c in keep]
    write_output(share_df, str(out), text_columns=text_cols)
    print(f"De-identified file to share with JHFRC (no address, no PHI): {out}")
    return out


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
            yield idx, normalize_text_columns(chunk.reset_index(drop=True))
    elif suffix in (".xlsx", ".xls"):
        kwargs = {"sheet_name": sheet_name} if sheet_name else {}
        frame = normalize_text_columns(pd.read_excel(path, dtype=str, **kwargs))
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
