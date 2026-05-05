# Windows VS_VERSION_INFO resource for Bullseye.exe.
#
# Wired into pyinstaller.spec via `EXE(version='version_info.py', ...)`.
# Right-click -> Properties -> Details on the resulting .exe will show
# this metadata.
#
# Format note: PyInstaller's loader for this file uses eval(), not
# exec() — so this file must be a single VSVersionInfo expression with
# only `# ...` comment lines around it. No imports, no assignments,
# no module-level statements. Update the version numbers in BOTH the
# `filevers`/`prodvers` tuples AND the FileVersion / ProductVersion
# strings together when bumping the release.
VSVersionInfo(
  ffi=FixedFileInfo(
    # Version 0.1.0.0 — keep in sync with strings below.
    filevers=(0, 1, 0, 0),
    prodvers=(0, 1, 0, 0),
    # Standard mask / flags / OS / type values for a Windows GUI app.
    # 0x40004 = VOS_NT_WINDOWS32; 0x1 = VFT_APP.
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          # 040904B0 = U.S. English, Unicode codepage. Standard for English-only apps.
          '040904B0',
          [
            StringStruct('CompanyName', 'Bullseye'),
            StringStruct('FileDescription', 'Bullseye desktop app'),
            StringStruct('FileVersion', '0.1.0.0'),
            StringStruct('InternalName', 'Bullseye'),
            StringStruct('LegalCopyright', 'Copyright (c) 2026 Bullseye'),
            StringStruct('OriginalFilename', 'Bullseye.exe'),
            StringStruct('ProductName', 'Bullseye'),
            StringStruct('ProductVersion', '0.1.0.0')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct('Translation', [0x409, 1200])])
  ]
)
