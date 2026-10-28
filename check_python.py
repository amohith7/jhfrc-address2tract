#!/usr/bin/env python
"""Check whether this Python can install and run the tool.

Run this BEFORE installing the requirements:

    python check_python.py

The tool needs Python 3.10 or newer; there is no upper limit. On 3.10-3.12
requirements.txt installs exact, tested versions, and on 3.13 and newer it
installs the newest releases that ship prebuilt installers for your Python. On
an older Python pip fails with a confusing error. This script tells you, in
plain language, whether your Python will work, before you hit that error.

It uses only the standard library and avoids new syntax on purpose, so it still
runs (and prints a helpful message) on older Python versions.
"""

import sys

MIN = (3, 10)
LINE = "=" * 60


def main():
    major, minor = sys.version_info[0], sys.version_info[1]
    current = "{0}.{1}.{2}".format(
        sys.version_info[0], sys.version_info[1], sys.version_info[2]
    )

    if (major, minor) >= MIN:
        print("OK: Python " + current + " is supported.")
        print("Next step:  pip install -r requirements.txt")
        return 0

    print(LINE)
    print("  Unsupported Python version: " + current)
    print(LINE)
    print("  This tool needs Python 3.10 or newer.")
    print("")
    print("  Install a supported version from:")
    print("      https://www.python.org/downloads/")
    print("  (on Windows, tick 'Add Python to PATH'), then run this check")
    print("  again with that Python before installing the requirements.")
    print(LINE)
    return 1


if __name__ == "__main__":
    sys.exit(main())
