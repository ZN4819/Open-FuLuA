from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


ROOT = Path(SPECPATH).parents[1]
BACKEND_ROOT = ROOT / "backend"
PACKAGING_ROOT = BACKEND_ROOT / "packaging"

PACKAGED_DATA_SOURCES = (
    ("templates/appendix_a/template_profile.json", "templates/appendix_a"),
    ("templates/appendix_a/record_templates.json", "templates/appendix_a"),
    ("templates/scoring/scoring_template_v1.xlsx", "templates/scoring"),
    ("templates/report/2023-2025.12.08/runtime_template.docx", "templates/report/2023-2025.12.08"),
    ("templates/report/2023-2025.12.08/field_dictionary.json", "templates/report/2023-2025.12.08"),
    ("templates/report/2023-2025.12.08/manifest.json", "templates/report/2023-2025.12.08"),
    ("templates/report/2023-2025.12.08/rule_hints.json", "templates/report/2023-2025.12.08"),
    ("templates/report/2023-2025.12.08/narrative_templates.json", "templates/report/2023-2025.12.08"),
    ("templates/report/2023-2025.12.08/asset_hashes.json", "templates/report/2023-2025.12.08"),
    (
        "templates/report/contracts/2023-2025.12.08/field_relation_matrix.v1.json",
        "templates/report/contracts/2023-2025.12.08",
    ),
    (
        "templates/report/contracts/2023-2025.12.08/derived_rule_matrix.v1.json",
        "templates/report/contracts/2023-2025.12.08",
    ),
    (
        "templates/report/contracts/2023-2025.12.08/r3_projection_context.v1.schema.json",
        "templates/report/contracts/2023-2025.12.08",
    ),
    (
        "backend/app/report_core/manifests/report-2023-2025.12.08.json",
        "app/report_core/manifests",
    ),
    ("scripts/word_refresh_report.ps1", "scripts"),
    ("frontend/dist", "frontend"),
)

datas = [(str(ROOT / source), destination) for source, destination in PACKAGED_DATA_SOURCES]

hiddenimports = [
    "app.main",
    "app.desktop_server",
    *collect_submodules("PIL"),
    *collect_submodules("lxml"),
    *collect_submodules("openpyxl"),
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
