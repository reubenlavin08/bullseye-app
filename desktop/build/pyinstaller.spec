# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Bullseye desktop app.
#
# Build (from this directory):
#     pyinstaller pyinstaller.spec --clean --noconfirm
#
# Output: dist/Bullseye.exe (single-file ~80MB executable).
# Then run installer.iss via Inno Setup to wrap in a Windows installer.
#
# Notes on the choices below:
#   - Single-file (`runtime_tmpdir=None`) so distribution is one .exe.
#     PyInstaller extracts to %TEMP%\_MEIxxxxxx on launch; that's the
#     standard tradeoff vs. one-folder mode.
#   - `console=False` — this is a GUI app; we don't want a black box.
#   - UPX disabled. UPX-compressed Windows EXEs trip a lot of AV
#     heuristics (especially Defender + SmartScreen). Saving 20% on
#     download size isn't worth false positives in user testing. Can
#     re-enable later with `upx_dir=` if size becomes a real issue.
#   - `runtime_hook.py` — fixes a couple of `Path(__file__).parents[3]`
#     constants in the source tree that don't survive freezing. See the
#     hook file for details.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Resolve project paths relative to this spec file so the build works
# from any cwd. PyInstaller exposes the spec's dir as SPECPATH.
SPEC_DIR = Path(SPECPATH).resolve()
DESKTOP = SPEC_DIR.parent
SRC = DESKTOP / "src"
ASSETS = DESKTOP / "assets"
CONFIG = DESKTOP / "config"

block_cipher = None


# --- Data files bundled into the exe ----------------------------------
# Tuples are (source_path_on_disk, dest_path_inside_bundle).
datas = [
    # Flask templates + static (referenced via webapp/__file__).
    (str(SRC / "webapp" / "templates"), "webapp/templates"),
    (str(SRC / "webapp" / "static"), "webapp/static"),
    # SQLite migrations (deal_finder.db.migrate uses __file__/migrations).
    (str(SRC / "deal_finder" / "db" / "migrations"), "deal_finder/db/migrations"),
    # Rejection filter config (runtime_hook redirects the lookup here).
    (str(CONFIG / "rejection_keywords.txt"), "config"),
    (str(CONFIG / "rejection_patterns.txt"), "config"),
    # Tray icon + window logo.
    (str(ASSETS / "logo.png"), "assets"),
    (str(ASSETS / "logo.ico"), "assets"),
]

# curl_cffi ships libcurl + a cacert bundle as data files that
# PyInstaller's static analyzer doesn't see. Skipping these gives
# "OSError: cacert.pem not found" at runtime.
datas += collect_data_files("curl_cffi")


# --- Hidden imports ---------------------------------------------------
# Modules PyInstaller's bytecode analysis misses, mostly because
# they're loaded via plugin lookup / runtime dispatch rather than a
# top-level `import`.
hiddenimports: list[str] = [
    # PyInstaller's own egg-info shim — some packages still reference it.
    "pkg_resources.py2_warn",

    # pystray picks its backend by platform at runtime via __getattr__.
    "pystray._win32",
    "pystray._util.win32",

    # plyer's notification backend dispatches by platform name.
    "plyer.platforms.win.notification",

    # PyWebView's Windows GUI uses the .NET WinForms backend.
    "webview.platforms.winforms",

    # APScheduler trigger types are imported by string ("interval", etc).
    "apscheduler.triggers.interval",
    "apscheduler.triggers.cron",
    "apscheduler.triggers.date",
    "apscheduler.executors.pool",
    "apscheduler.schedulers.blocking",
    "apscheduler.schedulers.background",
    "apscheduler.jobstores.memory",

    # keyring picks a Windows credential-vault backend at runtime.
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.fail",
    "keyring.backends.null",
]

# Sweep up all submodules of these packages — they all do plugin-style
# dispatch and missing one shows up as a confusing late ImportError.
for pkg in ("pystray", "plyer", "webview", "apscheduler", "keyring"):
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        # Failure to collect a package's submodules shouldn't kill the
        # whole build — we already added the most important ones above.
        pass


# --- Excludes ---------------------------------------------------------
# Things the dependency walker pulls in that we don't actually use.
# Removing them shrinks the bundle materially.
excludes = [
    "tkinter",           # ~10MB; we use PyWebView for the GUI
    "psycopg2",          # Postgres — replaced by sqlite3
    "psycopg2-binary",
    "matplotlib",        # data viz; not used
    "numpy",             # not used directly; ~30MB
    "pandas",            # not used; pulls numpy
    "scipy",             # not used
    "IPython",           # interactive shell; not used
    "jupyter",
    "notebook",
    "pytest",            # test runner; not in production
    "pytest_cov",
    "PIL.ImageQt",       # Qt bindings for PIL; we don't use Qt
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
]


a = Analysis(
    [str(SRC / "main.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(SPEC_DIR / "runtime_hook.py")],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="Bullseye",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                 # see header note: AV heuristics
    runtime_tmpdir=None,       # single-file: extract to %TEMP%
    console=False,             # no console window — this is a GUI app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS / "logo.ico"),
    version=str(SPEC_DIR / "version_info.py"),
)
