# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the one-file Windows release of Desktop Icon Billiards.

build.ps1 owns the release metadata and exports it to this spec:

    DIB_VERSION            release version, e.g. 0.1.0
    DIB_VERSION_INFO_FILE  absolute path to the generated build/version_info.txt

The version is read from ``dib/__init__.py`` by build.ps1 and passed through the
environment so it is never hardcoded here. Building without them is refused.

Release build:
    pwsh -File build.ps1
which runs:
    python -m PyInstaller --noconfirm --distpath dist --workpath build/pyinstaller DesktopIconBilliards.spec
"""
import os
import re

version = os.environ.get('DIB_VERSION', '').strip()
version_info_file = os.environ.get('DIB_VERSION_INFO_FILE', '').strip()

if not version:
    raise SystemExit(
        'DIB_VERSION is not set. Build through: pwsh -File build.ps1'
    )
if not re.fullmatch(r'\d+\.\d+\.\d+', version):
    raise SystemExit(
        f'DIB_VERSION is not major.minor.patch: {version!r}. '
        'Build through: pwsh -File build.ps1'
    )
if not version_info_file:
    raise SystemExit(
        'DIB_VERSION_INFO_FILE is not set. Build through: pwsh -File build.ps1'
    )
if not os.path.isfile(version_info_file):
    raise SystemExit(f'DIB_VERSION_INFO_FILE does not exist: {version_info_file}')

# Anchor the project paths to the spec file so the build also works when
# PyInstaller is invoked from another working directory. SPECPATH is injected by
# PyInstaller when this file is executed.
base_dir = SPECPATH
entry_script = os.path.join(base_dir, 'app.pyw')
icon_path = os.path.join(base_dir, 'assets', 'desktop-icon-billiards.ico')

exe_name = f'DesktopIconBilliards-{version}-win-x64'
if not os.path.isfile(icon_path):
    raise SystemExit(f'Icon asset is missing: {icon_path}')

print(f'[spec] building {exe_name} with version metadata {version_info_file}')

a = Analysis(
    [entry_script],
    pathex=[],
    binaries=[],
    datas=[(icon_path, 'assets'), (os.path.join(base_dir, 'assets', 'donation.jpg'), 'assets'),
           (os.path.join(base_dir, 'assets', 'desktop-icon-billiards.png'), 'assets')],
    hiddenimports=[
        # app.pyw dispatches --watchdog to this module from inside main(); list
        # it explicitly so the helper can never fall out of the frozen bundle.
        'dib.watchdog',
        # The GUI behind app.pyw (imported lazily in main()).
        'dib.ui',
        # comtypes creates its submodules dynamically at runtime.
        'comtypes.stream',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Optional pywin32 GUI helpers pull in MFC, which the game does not use.
    excludes=['win32ui'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# One-file build: the bootloader unpacks to a temp directory at runtime, so
# there is intentionally no COLLECT step here.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=exe_name,
    version=version_info_file,
    icon=icon_path,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
