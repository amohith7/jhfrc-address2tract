#!/usr/bin/env python
"""Check whether this Python can install and run the tool.

Run this BEFORE installing the requirements:

    python check_python.py

The pinned dependencies ship prebuilt installers only for Python 3.10, 3.11,
and 3.12. On other versions pip has to build them from source, which usually
fails with a confusing error. This script tells you, in plain language, whether
your Python will work, before you hit that error.

It uses only the standard library and avoids new syntax on purpose, so it still
runs (and prints a helpful message) on older Python versions.
"""

import sys

MIN = (3, 10)
MAX_EXCLUSIVE = (3, 13)  # 3.13 and newer are not supported by the pinned wheels
LINE = "=" * 60


def main():
    major, minor = sys.version_info[0], sys.version_info[1]
    current = "{0}.{1}.{2}".format(
        sys.version_info[0], sys.version_info[1], sys.version_info[2]
    )

    if MIN <= (major, minor) < MAX_EXCLUSIVE:
        print("OK: Python " + current + " is supported.")
        print("Next step:  pip install -r requirements.txt")
        return 0

    print(LINE)
    print("  Unsupported Python version: " + current)
    print(LINE)
    print("  This tool supports Python 3.10, 3.11, or 3.12.")
    if (major, minor) < MIN:
        print("  Your version is older than the supported range.")
    else:
        print("  Your version is newer than the pinned dependencies support.")
    print("")
    print("  Install a supported version from:")
    print("      https://www.python.org/downloads/")
    print("  (on Windows, tick 'Add Python to PATH'), then run this check")
    print("  again with that Python before installing the requirements.")
    print(LINE)
    return 1


if __name__ == "__main__":
    sys.exit(main())
