; Inno Setup script for Bullseye Windows installer.
;
; Build:
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;
; Output: Output/Bullseye-Setup.exe
;
; Notes:
;   - Drop-in single-file install: the only payload is dist\Bullseye.exe.
;   - Installs to Program Files\Bullseye by default, with a desktop +
;     Start Menu shortcut. The "start with Windows" task is opt-in
;     (default checked) and points the Startup folder shortcut at the
;     installed exe.
;   - Uninstall offers to wipe the per-user data directory
;     (%USERPROFILE%\.bullseye), which is where the SQLite DB and
;     scheduler logs live. We mark it [UninstallDelete] only with the
;     `dontcloseapplications` flag — Inno will skip the delete unless
;     the user actively confirms during uninstall via a custom task.

#define MyAppName       "Bullseye"
#define MyAppVersion    "0.1.0"
#define MyAppPublisher  "Bullseye"
#define MyAppURL        "https://getbullseye.app"
#define MyAppExeName    "Bullseye.exe"

[Setup]
AppId={{B23F1F0E-3A0E-4E3C-B7A0-7F6B7C8D9E10}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableDirPage=auto
DisableProgramGroupPage=yes
OutputBaseFilename={#MyAppName}-Setup
SetupIconFile=..\assets\logo.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2/ultra
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
WizardStyle=modern
; Allow either per-user or per-machine install. {autopf} resolves to
; Program Files for admin installs, %LOCALAPPDATA%\Programs for user.
; This avoids the "userstartup with admin privileges" warning and
; means the user can install without an UAC prompt if they want.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "startupicon"; Description: "Start {#MyAppName} automatically when I log in"; GroupDescription: "Startup options:"

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Registry]
; Custom URL protocol `bullseye://` — used by the Stripe success page
; to bring the desktop app back to the foreground after subscribe/
; checkout completes. Without this, the user lands on the website's
; upgrade-success.html and has to manually switch back to the app.
;
; HKCU is per-user (works under PrivilegesRequired=lowest); HKCR
; would need admin. This is the standard Windows-protocol-handler
; layout: Default = "URL:Bullseye Protocol", URL Protocol = "" empty
; key marker, DefaultIcon points at the exe, shell\open\command is
; what Windows runs when bullseye:// is activated.
Root: HKCU; Subkey: "Software\Classes\bullseye"; ValueType: string; ValueName: ""; ValueData: "URL:Bullseye Protocol"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\bullseye"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\bullseye\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKCU; Subkey: "Software\Classes\bullseye\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

[Run]
; Drop the `unchecked` flag so the "Launch Bullseye" checkbox is
; ticked by default at the end of install. User can still uncheck it.
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Per-user state (SQLite DB + logs) lives at %USERPROFILE%\.bullseye.
; We deliberately do NOT auto-delete it on uninstall — too easy to
; nuke real user data on a routine upgrade. To wipe by hand:
;   rmdir /s /q "%USERPROFILE%\.bullseye"
; (If we ever want a user-facing checkbox: add a [Code] section that
; asks during uninstall, then conditionally runs DelTree on confirm.)
