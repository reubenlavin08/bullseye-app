"""PyInstaller runtime hook for Bullseye.

Runs once at startup before user code executes. Fixes a handful of
hard-coded `Path(__file__).resolve().parents[N] / "..."` expressions in
the source tree that compute paths assuming a checked-out repo layout
(`desktop/config/`, `desktop/assets/`). When frozen, those modules live
inside the PyInstaller MEIPASS temp directory, so parents[3] points
above MEIPASS — which is wrong.

The fix is a post-import patch: we register a meta-import-hook that
swaps in the correct MEIPASS-relative path immediately after each
affected module is loaded. We deliberately do NOT modify the source
modules themselves (they need to keep working under `python
desktop/src/main.py`). This lives in the build/ directory and only runs
inside the bundle.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Only fire inside a PyInstaller bundle. `_MEIPASS` is set on `sys`
# both for one-file (extracted to a temp dir) and one-folder builds.
_MEIPASS = getattr(sys, "_MEIPASS", None)
if _MEIPASS:
    _BUNDLE = Path(_MEIPASS)

    # Help any subprocess / library that wants the asset root via env.
    os.environ.setdefault("BULLSEYE_BUNDLE_DIR", str(_BUNDLE))

    # ----- Patch deal_finder.scraper.rejection._DEFAULT_CONFIG_DIR -----
    # The module computes its config dir at import time from __file__,
    # which lands inside MEIPASS — parents[3] is the parent of MEIPASS,
    # not the bundle root. We override after import.
    def _patch_rejection() -> None:
        try:
            from deal_finder.scraper import rejection
            rejection._DEFAULT_CONFIG_DIR = _BUNDLE / "config"
            # Clear caches so the new path takes effect on first use.
            try:
                rejection.reset_cache()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001 — never let a fixup crash boot
            pass

    # ----- Patch deal_finder.tray.app._ICON_PATH ------------------------
    # Same problem — uses parent.parent.parent.parent / "assets" /
    # "logo.png" which resolves outside the bundle.
    def _patch_tray() -> None:
        try:
            from deal_finder.tray import app as tray_app
            tray_app._ICON_PATH = _BUNDLE / "assets" / "logo.png"
        except Exception:  # noqa: BLE001
            pass

    _patch_rejection()
    _patch_tray()
