# Build on Windows with Python 3.12 x64. Never cross-compile Windows executables on macOS.
from pathlib import Path
root = Path(SPECPATH)
a = Analysis(
    [str(root / 'launcher.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'st_catalog' / 'data'), 'st_catalog/data')],
    hiddenimports=['PySide6.QtWidgets', 'PySide6.QtGui', 'PySide6.QtCore'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtQml', 'PySide6.QtQuick'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='STCatalogManager',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='STCatalogManager')
