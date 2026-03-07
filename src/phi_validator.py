"""
PHI Validator

Screens input column names for indicators of protected health information (PHI)
or other sensitive personal data before processing begins.
"""

import re

PHI_INDICATORS = [
    "name", "first_name", "last_name", "firstname", "lastname",
    "patient_name", "patientname", "full_name", "fullname",
    "date_of_birth", "dob", "birth_date", "birthdate",
    "ssn", "social_security", "social_security_number",
    "medical_record_number", "mrn",
    "diagnosis", "diagnoses",
    "insurance", "insurance_id",
    "phone", "phone_number", "telephone",
    "email", "email_address",
    "notes", "clinical_notes",
]


def _matches_phi(column: str, indicator: str) -> bool:
    """
    Check whether a column name matches a PHI indicator.
    Matches are made at word boundaries (underscore-delimited tokens),
    so 'filename' does not match 'name', but 'patient_name' does.
    """
    col_norm = column.lower().strip().replace(" ", "_")
    indicator_parts = indicator.lower().replace(" ", "_").split("_")
    col_parts = col_norm.split("_")

    # Exact match
    if col_norm == "_".join(indicator_parts):
        return True

    # Match as a contiguous token sequence within the column name parts
    n = len(indicator_parts)
    for i in range(len(col_parts) - n + 1):
        if col_parts[i : i + n] == indicator_parts:
            return True

    return False


def check_for_phi(columns: list) -> list:
    """
    Check column names for PHI indicators.
    Returns a list of flagged column names.
    """
    flagged = []
    for col in columns:
        for indicator in PHI_INDICATORS:
            if _matches_phi(col, indicator):
                flagged.append(col)
                break
    return flagged


def validate_no_phi(columns: list) -> None:
    """
    Raise a ValueError if PHI-related columns are detected.
    The error message is written to be polite and clear for non-technical users.
    """
    flagged = check_for_phi(columns)
    if flagged:
        flagged_str = "\n".join(f'  - "{c}"' for c in flagged)
        raise ValueError(
            "\n"
            "The input file contains column(s) that may include sensitive personal information:\n"
            f"{flagged_str}\n\n"
            "To protect client privacy, this tool only accepts the minimum necessary information.\n"
            "Please remove any columns containing names, dates of birth, contact information,\n"
            "medical identifiers, or other sensitive data before processing.\n\n"
            "Allowed columns should include only:\n"
            "  - A unique identifier (e.g., client_id, case_number)\n"
            "  - Address fields (e.g., full_address, street, city, state, zip)\n\n"
            "If you need help preparing your file, please contact: mohith-addepalli@utc.edu\n"
        )
