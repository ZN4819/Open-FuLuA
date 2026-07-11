import ast
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DesktopPackagingContractTests(unittest.TestCase):
    def test_updater_is_runtime_dependency_and_main_preserves_controlled_quit_order(self) -> None:
        package = json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
        main = (ROOT / "desktop" / "src" / "main.ts").read_text(encoding="utf-8")
        updater = (ROOT / "desktop" / "src" / "updater.ts").read_text(encoding="utf-8")

        self.assertIn("electron-updater", package.get("dependencies", {}))
        self.assertNotIn("electron-updater", package.get("devDependencies", {}))
        self.assertIn('import electronUpdater = require("electron-updater")', main)
        self.assertIn("app.isPackaged", main)
        self.assertIn("GuardedStartupCoordinator", main)
        self.assertIn("GuardedStartupSingleFlight", main)
        self.assertIn("RecoverySessionGate", main)
        self.assertIn('ipcMain.handle("app:retry-backend"', main)
        self.assertEqual(main.count("new GuardedStartupSingleFlight("), 1)
        self.assertIn("enterGuarded: () => guardedStartupFlight.enter()", main)
        self.assertIn("await guardedStartupFlight.enter();", main)
        self.assertIn("await guardedStartupFlight.enter(isFirstRun);", main)
        self.assertLess(updater.rindex("prepareUpgrade"), updater.rindex("stopSidecar"))
        self.assertLess(updater.rindex("stopSidecar"), updater.rindex("clearRunMarker"))
        self.assertLess(updater.rindex("clearRunMarker"), updater.rindex("approveControlledQuit"))
        self.assertLess(updater.rindex("approveControlledQuit"), updater.rindex("quitAndInstall"))

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

    def test_builder_excludes_compiled_desktop_tests_and_source_maps_from_asar(self) -> None:
        config = (ROOT / "desktop" / "electron-builder.yml").read_text(encoding="utf-8")

        self.assertIn('"!dist/**/*.test.js"', config)
        self.assertIn('"!dist/**/*.test.js.map"', config)
        self.assertIn('"!dist/**/*.map"', config)
        self.assertIn('"!node_modules/**/*.map"', config)

    def test_builder_configures_unsigned_per_user_nsis_with_uninstall_data_retention(self) -> None:
        config = (ROOT / "desktop" / "electron-builder.yml").read_text(encoding="utf-8")

        self.assertRegex(config, r"(?m)^\s*-\s*target:\s*nsis\s*$")
        self.assertRegex(config, r"(?m)^nsis:\s*$")
        for setting, expected in (
            ("oneClick", "false"),
            ("perMachine", "false"),
            ("allowElevation", "false"),
            ("allowToChangeInstallationDirectory", "true"),
            ("createDesktopShortcut", "true"),
            ("createStartMenuShortcut", "true"),
        ):
            self.assertRegex(config, rf"(?m)^\s*{setting}:\s*{expected}\s*$")
        self.assertNotIn("deleteAppDataOnUninstall", config)
        self.assertRegex(config, r"(?m)^\s*uninstallDisplayName:\s*.+$")
        self.assertRegex(config, r"(?m)^\s*executableName:\s*FuLuA\s*$")
        self.assertIn("asar: true", config)
        for resource in ("../frontend/dist", "../artifacts/desktop/backend/fulua-backend"):
            self.assertIn(resource, config)

    def test_build_script_supports_nsis_and_verifies_both_expected_artifacts(self) -> None:
        script = (ROOT / "scripts" / "build_desktop.ps1").read_text(encoding="utf-8")
        package = json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))

        self.assertRegex(script, r"ValidateSet\([^)]*['\"]nsis['\"]")
        self.assertIn("package:nsis", script)
        self.assertIn("Setup executable was not generated", script)
        self.assertIn("win-unpacked\\FuLuA.exe", script)
        self.assertRegex(package["scripts"].get("package:nsis", ""), r"electron-builder.*--win\s+nsis\s+dir")

    def test_install_acceptance_script_is_safe_and_covers_uninstall_reinstall_data_retention(self) -> None:
        script_path = ROOT / "scripts" / "test_desktop_install.ps1"
        self.assertTrue(script_path.is_file(), "缺少桌面安装验收脚本")
        script = script_path.read_text(encoding="utf-8")

        for required in (
            "BuildIfMissing",
            "Resolve-SafeChildPath",
            "LOCALAPPDATA",
            "Get-NetTCPConnection",
            "/api/health",
            "/api/projects",
            "uninstall.exe",
            "数据保留",
            "重新安装",
            "taskkill",
        ):
            self.assertIn(required, script)
        self.assertIn("/S", script)
        self.assertIn("/D=", script)
        self.assertNotIn("Remove-Item -Recurse -Force $env:LOCALAPPDATA", script)

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

    def test_install_acceptance_waits_for_installer_and_uninstaller_processes(self) -> None:
        script = (ROOT / "scripts" / "test_desktop_install.ps1").read_text(encoding="utf-8")

        self.assertRegex(
            script,
            r"Start-Process\s+-FilePath\s+\$installer\s+-ArgumentList\s+@\([^)]*'/S'[^)]*\)\s+-Wait\s+-PassThru",
        )
        self.assertRegex(
            script,
            r"Start-Process\s+-FilePath\s+\$uninstaller\s+-ArgumentList\s+@\('/S'\)\s+-Wait\s+-PassThru",
        )

    def test_install_acceptance_decodes_health_json_as_utf8(self) -> None:
        script = (ROOT / "scripts" / "test_desktop_install.ps1").read_text(encoding="utf-8")

        self.assertIn("Add-Type -AssemblyName System.Net.Http", script)
        self.assertIn("System.Net.Http.HttpClient", script)
        self.assertIn("GetStringAsync", script)
        self.assertIn("ConvertFrom-Json", script)

    def test_install_acceptance_uses_ascii_project_marker_for_powershell_compatibility(self) -> None:
        script = (ROOT / "scripts" / "test_desktop_install.ps1").read_text(encoding="utf-8")

        self.assertRegex(script, r'\$ProjectName\s*=\s*"CD6-install-\$\(')

    def test_install_acceptance_checks_reinstalled_projects_item_by_item(self) -> None:
        script = (ROOT / "scripts" / "test_desktop_install.ps1").read_text(encoding="utf-8")

        self.assertIn("Where-Object { $_.name -eq $ProjectName }", script)

    def test_install_acceptance_rejects_reparse_points_before_cleanup(self) -> None:
        script = (ROOT / "scripts" / "test_desktop_install.ps1").read_text(encoding="utf-8")

        self.assertIn("Assert-NoReparsePoint", script)
        self.assertIn("FileAttributes]::ReparsePoint", script)
        self.assertIn("Remove-SafeTestPath -Path $SafetyRoot -SafeRoot $TemporaryRoot", script)
        owned_tree = re.search(r"function Remove-OwnedTree \{(?P<body>.*?)^\}", script, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(owned_tree)
        assert owned_tree is not None
        reparse_handler = re.search(
            r"if \(\(\$item\.Attributes -band \[System\.IO\.FileAttributes\]::ReparsePoint\) -ne 0\) \{(?P<body>.*?)^    \}",
            owned_tree.group("body"),
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(reparse_handler)
        assert reparse_handler is not None
        self.assertIn("throw", reparse_handler.group("body"))
        self.assertNotIn("Remove-Item", reparse_handler.group("body"))

    def test_install_acceptance_fails_and_preserves_tree_when_process_termination_cannot_be_confirmed(self) -> None:
        script = (ROOT / "scripts" / "test_desktop_install.ps1").read_text(encoding="utf-8")
        stop_tree = re.search(r"function Stop-TestProcessTree \{(?P<body>.*?)^\}", script, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(stop_tree)
        assert stop_tree is not None
        body = stop_tree.group("body")
        self.assertIn("$LASTEXITCODE", body)
        self.assertIn("WaitForExit", body)
        self.assertRegex(body, r"LASTEXITCODE\s*-ne\s*0\)\s*\{\s*throw")
        self.assertRegex(body, r"-not\s+\$Process\.HasExited\)\s*\{\s*throw")
        self.assertIn("$ProcessTerminationFailed = $false", script)
        self.assertIn("$ProcessTerminationFailed = $true", script)
        self.assertIn("if (-not $ProcessTerminationFailed)", script)
        self.assertIn("if ($cleanupAllowed)", script)

    def test_install_acceptance_rejects_non_program_resources_after_installation(self) -> None:
        script = (ROOT / "scripts" / "test_desktop_install.ps1").read_text(encoding="utf-8")

        self.assertIn("Assert-InstalledProgramResources", script)
        for forbidden in ("*.sqlite", "*.db", "storage", "logs", "backups", "migration", "fixtures", "*.docx", "~$*.docx"):
            self.assertIn(forbidden, script)
        self.assertIn("resources\\app.asar", script)
        self.assertIn("resources\\app-update.yml", script)
        self.assertIn("resources\\frontend", script)
        self.assertIn("resources\\backend", script)
        self.assertIn("_internal\\docx\\templates\\default.docx", script)
        self.assertIn("Assert-InstalledProgramResources -InstallRoot $InstallRoot", script)

    def test_install_acceptance_inspects_asar_and_rejects_test_content(self) -> None:
        script = (ROOT / "scripts" / "test_desktop_install.ps1").read_text(encoding="utf-8")

        self.assertIn("Assert-PackagedAsarContents", script)
        self.assertIn("@electron\\asar\\bin\\asar.js", script)
        self.assertIn(" list ", script)
        self.assertIn("*.test.js", script)
        self.assertIn("(^|/)(test|tests)(/|$)", script)
        for required_module in ("dist/main.js", "dist/preload.js", "dist/runtimeApi.js"):
            self.assertIn(required_module, script)
        self.assertIn("Assert-PackagedAsarContents -InstallRoot $InstallRoot", script)

    def test_user_installation_guide_discloses_default_test_icon(self) -> None:
        guide = (ROOT / "docs" / "客户端安装与卸载说明.md").read_text(encoding="utf-8")

        self.assertIn("默认图标", guide)
        self.assertIn("临时", guide)

    def test_user_installation_guide_marks_clean_environment_validation_as_pending(self) -> None:
        guide = (ROOT / "docs" / "客户端安装与卸载说明.md").read_text(encoding="utf-8")

        self.assertIn("本机临时用户目录验收", guide)
        self.assertIn("干净 Windows 用户/VM 验收待执行", guide)

    def test_readme_and_installation_guide_describe_cd6_user_installation(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        guide = (ROOT / "docs" / "客户端安装与卸载说明.md").read_text(encoding="utf-8")

        self.assertIn("CD-6", readme)
        self.assertIn(".\\scripts\\build_desktop.ps1 -Target nsis", readme)
        self.assertIn("docs/客户端安装与卸载说明.md", readme)
        self.assertNotIn("选择“当前用户”", guide)
        self.assertIn("自动按当前用户范围安装，无需管理员权限", guide)

    def test_pyinstaller_spec_imports_app_and_collects_runtime_assets(self) -> None:
        spec = (ROOT / "backend" / "packaging" / "fulua_backend.spec").read_text(encoding="utf-8")
        entrypoint = ROOT / "backend" / "packaging" / "backend_entry.py"

        self.assertTrue(entrypoint.is_file(), "缺少最小后端打包启动器")
        self.assertIn("from app.desktop_server import main", entrypoint.read_text(encoding="utf-8"))
        self.assertIn("backend_entry.py", spec)
        self.assertIn("app.main", spec)
        self.assertIn("template_profile.json", spec)
        self.assertIn("frontend", spec)
        self.assertRegex(spec, r"collect_submodules\([\"']PIL[\"']\)")
        self.assertRegex(spec, r"collect_submodules\([\"']lxml[\"']\)")

    def test_pyinstaller_datas_are_an_explicit_program_resource_allowlist(self) -> None:
        spec_path = ROOT / "backend" / "packaging" / "fulua_backend.spec"
        tree = ast.parse(spec_path.read_text(encoding="utf-8"))

        def assigns_name(target: ast.expr, name: str) -> bool:
            if isinstance(target, ast.Name):
                return target.id == name
            if isinstance(target, (ast.List, ast.Tuple)):
                return any(assigns_name(element, name) for element in target.elts)
            return False

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

        datas_assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and (
                any(assigns_name(target, "datas") for target in node.targets)
                if isinstance(node, ast.Assign)
                else assigns_name(node.target, "datas")
            )
        ]
        self.assertEqual(len(datas_assignments), 1, "datas 必须且只能赋值一次")

        datas_assignment = datas_assignments[0]
        self.assertIsInstance(datas_assignment, ast.Assign)
        assert isinstance(datas_assignment, ast.Assign)
        self.assertIsInstance(datas_assignment.value, ast.ListComp)
        assert isinstance(datas_assignment.value, ast.ListComp)
        self.assertEqual(len(datas_assignment.value.generators), 1)
        generator = datas_assignment.value.generators[0]
        self.assertIsInstance(generator.iter, ast.Name)
        assert isinstance(generator.iter, ast.Name)
        self.assertEqual(generator.iter.id, "PACKAGED_DATA_SOURCES")

        datas_augmented_assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.AugAssign) and assigns_name(node.target, "datas")
        ]
        self.assertEqual(datas_augmented_assignments, [], "禁止使用 += 修改 datas")

        datas_mutations = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "datas"
            and node.func.attr in {"append", "extend"}
        ]
        self.assertEqual(datas_mutations, [], "禁止在白名单推导后追加或扩展 datas")

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

    def test_build_script_clears_only_managed_electron_output_before_packaging(self) -> None:
        script = (ROOT / "scripts" / "build_desktop.ps1").read_text(encoding="utf-8")

        cleanup_index = script.find("Remove-ManagedBuildDirectory -WorkspaceRoot $Root -CandidatePath $ElectronOutput")
        packaging_index = script.find("npm --prefix desktop run package:")
        self.assertGreaterEqual(cleanup_index, 0)
        self.assertGreater(packaging_index, cleanup_index)
        self.assertNotIn("Remove-Item -LiteralPath $ArtifactsRoot -Recurse", script)

    def test_build_output_guard_enforces_containment_and_reparse_boundaries(self) -> None:
        helper = ROOT / "scripts" / "build_output_guard.ps1"
        self.assertTrue(helper.read_bytes().isascii(), "Windows PowerShell 5.1 must load this helper without a UTF-8 BOM")
        self.assertTrue(helper.is_file())
        self.assertNotIn("Remove-Item -LiteralPath $candidate -Recurse", helper.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            electron = workspace / "artifacts" / "desktop" / "electron"
            sibling = workspace / "artifacts" / "desktop" / "keep.txt"
            electron.mkdir(parents=True)
            (electron / "stale.exe").write_text("stale", encoding="utf-8")
            sibling.write_text("keep", encoding="utf-8")

            prefix = f". '{str(helper).replace(chr(39), chr(39) * 2)}'; "
            valid = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", prefix + f"Remove-ManagedBuildDirectory -WorkspaceRoot '{workspace}' -CandidatePath '{electron}'"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            )
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertFalse(electron.exists())
            self.assertTrue(sibling.is_file())

            managed_root = workspace / "artifacts" / "desktop"
            rejected = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", prefix + f"Remove-ManagedBuildDirectory -WorkspaceRoot '{workspace}' -CandidatePath '{managed_root}'"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertTrue(sibling.is_file())

            external = Path(temporary) / "external"
            external.mkdir()
            sentinel = external / "sentinel.txt"
            sentinel.write_text("outside", encoding="utf-8")
            electron.mkdir()
            internal_junction = electron / "external-link"
            junction_test = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-Command",
                    prefix
                    + f"$j=New-Item -ItemType Junction -Path '{internal_junction}' -Target '{external}'; "
                    + f"try {{ Remove-ManagedBuildDirectory -WorkspaceRoot '{workspace}' -CandidatePath '{electron}'; exit 91 }} "
                    + f"catch {{ exit 0 }} finally {{ if (Test-Path -LiteralPath '{internal_junction}') {{ Remove-Item -LiteralPath '{internal_junction}' -Force }} }}",
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            )
            self.assertEqual(junction_test.returncode, 0, junction_test.stderr)
            self.assertTrue(sentinel.is_file())

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
