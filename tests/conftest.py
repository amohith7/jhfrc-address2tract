"""Shared test configuration for imports from the repository source tree."""

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

for import_path in (PROJECT_ROOT, SRC_DIR):
    path_text = str(import_path)
    if path_text not in sys.path:
        sys.path.insert(0, path_text)
