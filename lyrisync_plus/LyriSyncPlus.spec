# LyriSyncPlus.spec
from PyInstaller.utils.hooks import collect_submodules, collect_data_files
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
import os

# Collect submodules to avoid missing imports at runtime
hidden = []
hidden += collect_submodules('aiohttp')
hidden += collect_submodules('websockets')
hidden += collect_submodules('ttkbootstrap')
hidden += collect_submodules('PIL')  # Pillow

# Optional: if you use yaml loaders explicitly
hidden += ['yaml', 'yaml.loader', 'yaml.cyaml']

# Collect data files (themes for ttkbootstrap, PIL data, etc.)
datas = []
datas += collect_data_files('ttkbootstrap', include_py_files=True)
datas += collect_data_files('PIL', include_py_files=True)

# Bundle your splash image if present
if os.path.exists('splash.png'):
    datas.append(('splash.png', '.'))

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LyriSyncPlus',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # no console window
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LyriSyncPlus',
)
