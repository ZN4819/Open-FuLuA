from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).parents[1]
BACKEND_ROOT = ROOT / "backend"
PACKAGING_ROOT = BACKEND_ROOT / "packaging"

PACKAGED_DATA_SOURCES = (
    ("templates/appendix_a/template_profile.json", "templates/appendix_a"),
    ("templates/appendix_a/record_templates.json", "templates/appendix_a"),
    ("frontend/dist", "frontend"),
)

datas = [(str(ROOT / source), destination) for source, destination in PACKAGED_DATA_SOURCES]

hiddenimports = [
    "app.main",
    "app.desktop_server",
    *collect_submodules("PIL"),
    *collect_submodules("lxml"),
]

analysis = Analysis(
    [str(PACKAGING_ROOT / "backend_entry.py")],
    pathex=[str(BACKEND_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="fulua-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="fulua-backend",
)
