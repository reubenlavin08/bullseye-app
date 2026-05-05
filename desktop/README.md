# Bullseye desktop app

Python 3.12+ desktop app, ships as a Windows installer.

## Architecture

- **Entry point**: `src/main.py` orchestrates the full process tree:
  Sentry → DB migrate → auth check → Flask thread → scheduler thread →
  digest worker thread → tray thread → PyWebView window.
- **Shell**: PyWebView native window pointing at `localhost:RANDOM_PORT`
  Flask UI. The Flask app is the SAME UI you'd hit in a browser — the
  native window just hosts it.
- **State**: SQLite at `~/.bullseye/bullseye.db` (Windows: `%USERPROFILE%`).
- **Cloud calls**: every external dependency (eBay comps, email send, license
  check, telemetry) goes through `src/deal_finder/cloud/` modules which post
  to Supabase Edge Functions with a JWT bearer token.

## Running locally during dev

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
python -m src.main
```

## Building the Windows installer

The build is a two-stage pipeline orchestrated by `build/build_windows.bat`:

1. **PyInstaller** packs `src/main.py` plus all dependencies, the Flask
   templates / static assets, the SQLite migrations, the rejection
   filter config, and the tray icon into a single ~30 MB
   `dist/Bullseye.exe`.
2. **Inno Setup** wraps that exe in a Windows installer
   (`Output/Bullseye-Setup.exe`, ~31 MB) that creates Start Menu and
   desktop shortcuts and optionally a Startup-folder shortcut for
   auto-launch on login.

### Prerequisites (one-time)

- Python 3.12 venv with `requirements.txt` installed.
- [Inno Setup 6](https://jrsoftware.org/isdl.php) installed at the
  default path `C:\Program Files (x86)\Inno Setup 6`.

### Build

```bash
cd build
build_windows.bat
```

Outputs:

- `build/dist/Bullseye.exe` — single-file executable; double-clickable.
  Extracts to `%TEMP%\_MEIxxxxxx` on launch (standard PyInstaller
  one-file behaviour).
- `build/Output/Bullseye-Setup.exe` — the file you actually distribute.

### Files in `build/`

| File | Owner | What it does |
| --- | --- | --- |
| `pyinstaller.spec` | step 10 | Full PyInstaller build recipe (data files, hidden imports, excludes). |
| `runtime_hook.py` | step 10 | Patches a couple of `Path(__file__).parents[3]` constants so `config/` and `assets/` lookups resolve inside the bundle. |
| `version_info.py` | step 10 | Windows VS_VERSION_INFO resource (CompanyName, FileVersion, etc — visible in Properties → Details). |
| `installer.iss` | step 10 | Inno Setup script: shortcuts, autostart, uninstall behaviour. |
| `build_windows.bat` | step 10 | Runs PyInstaller then ISCC end-to-end. |

### Things to know about the bundle

- **No console window** — `console=False` in the spec.
- **No UPX compression** — UPX trips Windows Defender / SmartScreen
  heuristics. We trade ~20% size for fewer false-positive AV reports.
- **Excluded packages**: `tkinter`, `numpy`, `pandas`, `matplotlib`,
  `psycopg2*`, `IPython`, `jupyter`, Qt bindings — none are used by
  the runtime. Saves roughly 40 MB of dead weight.
- **Per-user state** at `%USERPROFILE%\.bullseye\` (SQLite DB +
  scheduler logs) is preserved across uninstall/reinstall on purpose.
  To wipe by hand: `rmdir /s /q "%USERPROFILE%\.bullseye"`.

## Tests

219 tests carry over from the deal_finder personal tool. Some need rewriting
for SQLite — that's tracked in Phase 2 of the blueprint.

```bash
pytest
```
