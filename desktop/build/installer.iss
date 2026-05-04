; Inno Setup script for Bullseye Windows installer.
;
; Build:
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;
; Output: Output/Bullseye-Setup.exe

[Setup]
AppName=Bullseye
AppVersion=0.1.0
AppPublisher=Bullseye
AppPublisherURL=https://bullseye.app
DefaultDirName={autopf}\Bullseye
DefaultGroupName=Bullseye
OutputBaseFilename=Bullseye-Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64
DisableProgramGroupPage=yes
SetupIconFile=..\assets\logo.ico
UninstallDisplayIcon={app}\Bullseye.exe
WizardStyle=modern

[Files]
Source: "dist\Bullseye.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Bullseye"; Filename: "{app}\Bullseye.exe"
Name: "{commondesktop}\Bullseye"; Filename: "{app}\Bullseye.exe"; Tasks: desktopicon
Name: "{userstartup}\Bullseye"; Filename: "{app}\Bullseye.exe"; Tasks: startup

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"
Name: "startup"; Description: "Start Bullseye automatically when I log in"; GroupDescription: "Startup options:"

[Run]
Filename: "{app}\Bullseye.exe"; Description: "Launch Bullseye"; Flags: postinstall nowait skipifsilent
