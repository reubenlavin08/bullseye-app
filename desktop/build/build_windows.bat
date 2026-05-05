@echo off
REM ===========================================================================
REM Build the Windows executable + installer for Bullseye.
REM
REM Output:
REM   dist\Bullseye.exe          (single-file executable, ~30-40 MB)
REM   Output\Bullseye-Setup.exe  (installer for distribution, ~25-35 MB)
REM
REM Prereqs (one-time):
REM   - Python venv at ..\..\..\deal_finder\.venv with requirements.txt installed
REM     (the bullseye + deal_finder repos share a single venv).
REM   - Inno Setup 6 installed at "C:\Program Files (x86)\Inno Setup 6".
REM
REM Re-run this whenever you change anything under desktop\src or
REM desktop\assets — both stages are fast (~75 sec) on a warm cache.
REM ===========================================================================

setlocal
cd /d %~dp0

REM Use the venv's Python explicitly. Avoids "wrong site-packages"
REM problems if the user has another Python on PATH that doesn't have
REM PyInstaller installed.
set "VENV_PY=..\..\..\deal_finder\.venv\Scripts\python.exe"
if not exist "%VENV_PY%" (
    echo ERROR: venv Python not found at %VENV_PY%
    echo Activate or create a venv with the desktop\requirements.txt deps.
    exit /b 1
)

set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (
    echo ERROR: Inno Setup 6 not found at "%ISCC%".
    echo Install from https://jrsoftware.org/isdl.php
    exit /b 1
)

echo.
echo [1/2] Running PyInstaller...
"%VENV_PY%" -m PyInstaller pyinstaller.spec --noconfirm
if errorlevel 1 goto error

if not exist "dist\Bullseye.exe" (
    echo ERROR: PyInstaller succeeded but dist\Bullseye.exe is missing.
    exit /b 1
)

echo.
echo [2/2] Running Inno Setup...
"%ISCC%" installer.iss
if errorlevel 1 goto error

if not exist "Output\Bullseye-Setup.exe" (
    echo ERROR: Inno Setup succeeded but Output\Bullseye-Setup.exe is missing.
    exit /b 1
)

echo.
echo ============================================================
echo Build complete.
echo   Executable: %~dp0dist\Bullseye.exe
echo   Installer:  %~dp0Output\Bullseye-Setup.exe
echo ============================================================
exit /b 0

:error
echo.
echo BUILD FAILED.
exit /b 1
