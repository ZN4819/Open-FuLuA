import ast
import json
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DesktopPackagingContractTests(unittest.TestCase):
    def test_desktop_workspace_declares_pinned_windows_build_toolchain(self) -> None:
        package_path = ROOT / "desktop" / "package.json"
        self.assertTrue(package_path.is_file(), "缺少 desktop/package.json")

        package = json.loads(package_path.read_text(encoding="utf-8"))
        tsconfig = json.loads((ROOT / "desktop" / "tsconfig.json").read_text(encoding="utf-8"))
        self.assertTrue(package.get("private"))
        self.assertEqual(package.get("main"), "dist/main.js")
        self.assertEqual(package.get("engines", {}).get("node"), ">=22.12.0 <25")
        self.assertRegex(package.get("scripts", {}).get("typecheck", ""), r"\btsc\b.*--noEmit")

        dev_dependencies = package.get("devDependencies", {})
        for dependency in ("electron", "electron-builder", "typescript"):
            version = dev_dependencies.get(dependency, "")
            self.assertRegex(version, r"^\d+\.\d+\.\d+$", f"{dependency} 必须固定到精确版本")

        compiler_options = tsconfig.get("compilerOptions", {})
        self.assertEqual(compiler_options.get("module"), "Node16")
        self.assertEqual(compiler_options.get("moduleResolution"), "Node16")

    def test_electron_window_is_isolated_and_preload_api_is_restricted(self) -> None:
        main_source = (ROOT / "desktop" / "src" / "main.ts").read_text(encoding="utf-8")
        preload_source = (ROOT / "desktop" / "src" / "preload.ts").read_text(encoding="utf-8")

        self.assertIn("正在启动附录A编写工具", main_source)
        self.assertRegex(main_source, r"contextIsolation\s*:\s*true")
        self.assertRegex(main_source, r"nodeIntegration\s*:\s*false")
        self.assertIn("mkdir(logsDirectory, { recursive: true })", main_source)
        self.assertIn("contextBridge.exposeInMainWorld", preload_source)
        self.assertIn("getVersion", preload_source)
        self.assertIn("openLogsDirectory", preload_source)
        self.assertNotRegex(preload_source, r"\brequire\s*\(")

    def test_builder_collects_only_program_resources_for_win_x64(self) -> None:
        config = (ROOT / "desktop" / "electron-builder.yml").read_text(encoding="utf-8")

        self.assertRegex(config, r"(?m)^win:\s*$")
        self.assertRegex(config, r"(?m)^\s*-\s*target:\s*dir\s*$")
        self.assertRegex(config, r"(?m)^\s*arch:\s*x64\s*$")
        self.assertIn("../frontend/dist", config)
        self.assertIn("../artifacts/desktop/backend/fulua-backend", config)
        for forbidden in ("storage/", "backend/data", "tests/", "*.db", "*.log"):
            self.assertNotIn(forbidden, config)

    def test_pyinstaller_spec_imports_app_and_collects_runtime_assets(self) -> None:
        spec = (ROOT / "backend" / "packaging" / "fulua_backend.spec").read_text(encoding="utf-8")
        entrypoint = ROOT / "backend" / "packaging" / "backend_entry.py"

        self.assertTrue(entrypoint.is_file(), "缺少最小后端打包启动器")
        self.assertIn("from app.main import app", entrypoint.read_text(encoding="utf-8"))
        self.assertIn("backend_entry.py", spec)
        self.assertIn("app.main", spec)
        self.assertIn("template_profile.json", spec)
        self.assertIn("frontend", spec)
        self.assertRegex(spec, r"collect_submodules\([\"']PIL[\"']\)")
        self.assertRegex(spec, r"collect_submodules\([\"']lxml[\"']\)")

    def test_pyinstaller_datas_are_an_explicit_program_resource_allowlist(self) -> None:
        spec_path = ROOT / "backend" / "packaging" / "fulua_backend.spec"
        tree = ast.parse(spec_path.read_text(encoding="utf-8"))
        assignment = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "PACKAGED_DATA_SOURCES"
                    for target in node.targets
                )
            ),
            None,
        )

        self.assertIsNotNone(assignment, "PyInstaller datas 必须来自显式资源白名单")
        assert assignment is not None
        sources = ast.literal_eval(assignment.value)
        self.assertEqual(
            sources,
            (
                ("templates/appendix_a/template_profile.json", "templates/appendix_a"),
                ("templates/appendix_a/record_templates.json", "templates/appendix_a"),
                ("frontend/dist", "frontend"),
            ),
        )

        serialized_sources = "\n".join(source for source, _destination in sources).lower()
        for forbidden in (
            "storage",
            "backend/data",
            "tests",
            "fixture",
            ".db",
            ".log",
            "附录a编写.docx",
            "user",
        ):
            self.assertNotIn(forbidden, serialized_sources)

    def test_build_script_runs_frontend_before_pyinstaller_and_fails_fast(self) -> None:
        script = (ROOT / "scripts" / "build_desktop.ps1").read_text(encoding="utf-8")

        frontend_index = script.find("npm --prefix frontend run build")
        pyinstaller_index = script.find("-m PyInstaller")
        electron_index = script.find("npm --prefix desktop")
        self.assertGreaterEqual(frontend_index, 0)
        self.assertGreater(pyinstaller_index, frontend_index)
        self.assertGreater(electron_index, pyinstaller_index)
        self.assertIn("$ErrorActionPreference = 'Stop'", script)
        self.assertRegex(script, re.escape("$LASTEXITCODE") + r"\s*-ne\s*0")
        self.assertIn("artifacts\\desktop", script)

    def test_build_script_is_parseable_by_windows_powershell(self) -> None:
        script_path = ROOT / "scripts" / "build_desktop.ps1"
        command = (
            "$null = [scriptblock]::Create((Get-Content -Raw -LiteralPath '"
            + str(script_path).replace("'", "''")
            + "'))"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
