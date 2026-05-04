"""Test fixtures + path setup for the desktop/ test suite.

Adds `src/` to sys.path so `from deal_finder...` imports resolve
without requiring a pip-installed package. The personal tool used a
proper pyproject.toml + editable install; here we keep things simple
and rely on this conftest.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `src/` importable when running pytest from desktop/ or from
# the repo root.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
