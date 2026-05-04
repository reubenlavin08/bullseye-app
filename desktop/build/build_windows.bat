@echo off
REM Build the Windows executable + installer.
REM
REM Prereqs:
REM   - Python venv active with requirements.txt installed
REM   - PyInstaller installed (in requirements.txt)
REM   - Inno Setup 6 installed at "C:\Program Files (x86)\Inno Setup 6"
REM
REM Output:
REM   dist\Bullseye.exe          (single-file executable)
REM   Output\Bullseye-Setup.exe  (installer for distribution)

cd %~dp0

echo [1/2] Running PyInstaller...
pyinstaller pyinstaller.spec --clean
if errorlevel 1 goto error

echo [2/2] Running Inno Setup...
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
if errorlevel 1 goto error

echo.
echo Build complete.
echo   Executable: %~dp0dist\Bullseye.exe
echo   Installer:  %~dp0Output\Bullseye-Setup.exe
exit /b 0

:error
echo.
echo BUILD FAILED.
exit /b 1
