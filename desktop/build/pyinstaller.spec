# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Bullseye desktop app.
#
# Build:
#     pyinstaller pyinstaller.spec --clean
#
# Output: dist/Bullseye.exe (single-file ~80MB executable).
# Then run installer.iss via Inno Setup to wrap in an installer.

# from PyInstaller.utils.hooks import collect_data_files
#
# a = Analysis(
#     ['../src/main.py'],
#     pathex=['../src'],
#     binaries=[],
#     datas=[
#         ('../src/webapp/templates', 'webapp/templates'),
#         ('../src/webapp/static', 'webapp/static'),
#         ('../src/deal_finder/db/migrations', 'deal_finder/db/migrations'),
#         ('../assets/logo.png', 'assets'),
#         ('../assets/logo.ico', 'assets'),
#     ] + collect_data_files('curl_cffi'),
#     hiddenimports=[
#         'pkg_resources.py2_warn',
#         'pystray._win32',
#         'plyer.platforms.win.notification',
#     ],
#     excludes=['tkinter'],   # we don't use it; saves ~10MB
# )
#
# pyz = PYZ(a.pure, a.zipped_data)
#
# exe = EXE(
#     pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
#     name='Bullseye',
#     console=False,                # no console window on Windows
#     icon='../assets/logo.ico',
#     upx=True,                     # compress; saves ~20%
#     runtime_tmpdir=None,
# )
