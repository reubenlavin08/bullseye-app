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

```bash
cd build
build_windows.bat
# produces Output/Bullseye-Setup.exe
```

## Tests

219 tests carry over from the deal_finder personal tool. Some need rewriting
for SQLite — that's tracked in Phase 2 of the blueprint.

```bash
pytest
```
