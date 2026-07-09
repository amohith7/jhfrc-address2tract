"""
Street Normalization

Conservative street-string cleanup used ONLY when retrying records that the
primary Census batch geocoder failed to match. It is never applied to records
that already matched, and it operates on a STREET string only (never a full
"street, city, state zip" address), so it cannot corrupt a city/state token or
regress a good result.

The rules are deliberately narrow and safe:
  - expand a street-type abbreviation only when it is the LAST token of the
    street (its true street-suffix position), e.g. "... Pk" -> "... Pike";
    never expand an abbreviation used as an interior/leading word such as the
    "St" in "St Louis" or the "Dr" in "Dr M L King Blvd".
  - fuse consecutive single-letter initials with periods ("B.B." -> "BB");
    leave a lone initial ("N. Main") untouched so nothing is glued together.
  - normalize whitespace.
"""

from __future__ import annotations

import re

# Street-type suffix abbreviations -> full form (matched only as the LAST token).
SUFFIX_EXPANSIONS = {
    "st": "Street",
    "ave": "Avenue",
    "av": "Avenue",
    "blvd": "Boulevard",
    "dr": "Drive",
    "rd": "Road",
    "ln": "Lane",
    "ct": "Court",
    "pl": "Place",
    "pkwy": "Parkway",
    "pky": "Parkway",
    "hwy": "Highway",
    "sq": "Square",
    "ter": "Terrace",
    "trl": "Trail",
    "cir": "Circle",
    "pk": "Pike",  # regional (e.g. Nashville "... Pk" = Pike)
}


def _expand_trailing_suffix(text: str) -> str:
    """Expand a street-type abbreviation only when it is the final token."""
    parts = text.split()
    if not parts:
        return text
    last = parts[-1].rstrip(".").lower()
    if last in SUFFIX_EXPANSIONS:
        parts[-1] = SUFFIX_EXPANSIONS[last]
        return " ".join(parts)
    return text


def _fuse_initials(text: str) -> str:
    """Fuse consecutive single-letter initials: 'B.B.' -> 'BB'.

    Only touches runs of at least two single-letter-with-period tokens, so a
    lone directional initial like 'N.' is left intact (no risk of gluing it to
    the next word).
    """
    return re.sub(
        r"\b([A-Za-z])\.([A-Za-z])\.", lambda m: m.group(1) + m.group(2) + " ", text,
    )


def normalize_street(street: str) -> str:
    """Return a conservatively normalized STREET string for a retry attempt.

    Pass only a street (not a full "street, city, state zip" address). Returns
    the input unchanged (aside from whitespace) if no rule applies.

    NOTE: a leading-duplicate-house-number rule was intentionally removed. Its
    only trigger cases were unrecoverable garbage ("1305 1305"), while it
    corrupted real names whose street begins with a number equal to the house
    number ("5 5 Points Rd" -> the house number was dropped). No upside, real
    downside, so it is gone.
    """
    if street is None:
        return ""
    s = " ".join(str(street).split())  # collapse whitespace
    if not s:
        return ""
    s = _fuse_initials(s)
    s = _expand_trailing_suffix(s)
    return " ".join(s.split())
