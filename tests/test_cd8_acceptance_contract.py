from __future__ import annotations

import re
import subprocess
import unittest
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Cd8AcceptanceContractTests(unittest.TestCase):
    def test_acceptance_script_covers_packaged_business_migration_and_reinstall_loops(self) -> None:
        script_path = ROOT / "scripts" / "test_desktop_acceptance.ps1"
        self.assertTrue(script_path.is_file(), "缺少 CD-8 桌面客户端验收脚本")
        script = script_path.read_text(encoding="utf-8")

        for required in (
            "InstallerPath",
            "BuildIfMissing",
            "Wait-DesktopHealth",
            "Assert-InstalledProgramResources",
            "Assert-PackagedAsarContents",
            "/api/projects",
            "/sections/A-1",
            "/api/projects/{0}/evidence",
            "/validate",
            "/exports/docx?mode=",
            "-Mode editable",
            "-Mode final",
            "/api/imports/docx",
            "/api/imports/{0}/project",
            "/api/runtime/migration/preflight",
            "/api/runtime/migration",
            "x-fulua-session-token",
            "uninstall.exe",
            "reinstall",
        ):
            self.assertIn(required, script)
        self.assertRegex(script, r"Stop-TestProcessTree\s+\$AuthorLaunch.*Start-InstalledClient", re.DOTALL)
        self.assertIn("source_database_hash_before", script)
        self.assertIn("source_database_hash_after", script)
        self.assertIn("Assert-ProjectRetained", script)
        self.assertIn("Assert-BusinessState", script)
        self.assertIn("template_slots_checked", script)
        self.assertIn("business_state_reopen", script)
        self.assertIn("business_state_migration", script)
        self.assertIn("business_state_reinstall", script)
        self.assertIn("$projectsResponse | ForEach-Object { $_ }", script)
        self.assertIn(r"resources\backend\fulua-backend.exe", script)
        self.assertNotIn(r"resources\backend\fulua-backend\fulua-backend.exe", script)
        self.assertIn("'.png' { 'image/png' }", script)
        self.assertIn("'.docx' { 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }", script)

    def test_acceptance_script_uses_owned_non_reparse_temp_tree_and_parses(self) -> None:
        script_path = ROOT / "scripts" / "test_desktop_acceptance.ps1"
        script = script_path.read_text(encoding="utf-8")

        for required in (
            "Resolve-SafeChildPath",
            "Assert-NoReparsePoint",
            "Remove-OwnedTree",
            "Remove-SafeTestPath",
            "FileAttributes]::ReparsePoint",
            "fulua-cd8-acceptance-",
            "ProcessTerminationFailed",
            "cleanupAllowed",
        ):
            self.assertIn(required, script)
        self.assertNotIn("Remove-Item -Recurse", script)
        self.assertNotIn("Remove-Item -Recurse -Force $env:LOCALAPPDATA", script)
        self.assertIn("Remove-SafeTestPath -Path $SafetyRoot -SafeRoot $TemporaryRoot", script)

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

    def test_acceptance_script_emits_machine_readable_factual_evidence(self) -> None:
        script = (ROOT / "scripts" / "test_desktop_acceptance.ps1").read_text(encoding="utf-8")

        for field in (
            "status",
            "failure_message",
            "installer_sha512",
            "signatures",
            "package_contents_checked",
            "project_saved",
            "image_uploaded",
            "validation_checked",
            "editable_exported",
            "final_exported",
            "docx_imported",
            "close_reopen_checked",
            "migration_preflight_checked",
            "migration_checked",
            "uninstall_data_retained",
            "reinstall_checked",
            "manual_items",
        ):
            self.assertRegex(script, rf"(?m)^\s*{re.escape(field)}\s*=", field)
        self.assertIn("ConvertTo-Json -Depth", script)
        self.assertIn("$cleanupAllowed = ($Result.status -eq 'passed')", script)
        self.assertIn("Get-AuthenticodeSignature", script)
        self.assertRegex(script, r"Get-FileHash\b[^\r\n]*-Algorithm\s+SHA512")

    def test_rc_version_evidence_and_release_workflow_are_consistent(self) -> None:
        package = json.loads((ROOT / "desktop" / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / "desktop" / "package-lock.json").read_text(encoding="utf-8"))
        script = (ROOT / "scripts" / "test_desktop_acceptance.ps1").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")

        self.assertEqual(package["version"], "0.1.0-rc.1")
        self.assertEqual(lock["version"], "0.1.0-rc.1")
        self.assertEqual(lock["packages"][""]["version"], "0.1.0-rc.1")
        self.assertIn("EvidenceOutputPath", script)
        for field in ("source_commit", "version", "installer_sha512", "signatures"):
            self.assertIn(field, script)
        self.assertIn("--allow-same-version", workflow)
        self.assertIn("test_desktop_acceptance.ps1", workflow)
        self.assertIn("acceptance-evidence.json", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)
        self.assertNotIn("NotRequiredPrerelease", workflow)

    def test_dev_smoke_is_reproducible_and_machine_readable(self) -> None:
        smoke = ROOT / "scripts" / "test_dev_smoke.ps1"
        self.assertTrue(smoke.is_file())
        text = smoke.read_text(encoding="utf-8")
        for required in ("start_dev.ps1", "/api/health", "runtime_mode", "frontend_status", "ConvertTo-Json", "taskkill"):
            self.assertIn(required, text)
        for required in ("ProcessInfoPath", "RequireNew", "start_ticks", "Get-Process -Id"):
            self.assertIn(required, text)
        self.assertNotIn("OwningProcess", text)
        self.assertIn("EvidenceOutputPath", text)
        for field in ("source_commit", "version", "status", "health"):
            self.assertRegex(text, rf"(?m)^\s*{field}\s*=", field)
        self.assertIn("ConvertTo-Json -Depth", text)
        command = "$null=[scriptblock]::Create((Get-Content -Raw -Encoding UTF8 -LiteralPath '" + str(smoke).replace("'", "''") + "'))"
        result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_release_workflow_publishes_dev_smoke_and_complete_dynamic_report(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")

        self.assertIn("test_dev_smoke.ps1", workflow)
        self.assertIn("dev-smoke-evidence.json", workflow)

        self.assertRegex(
            workflow,
            re.compile(r"Build NSIS and decide signing gate.*?test_dev_smoke\.ps1", re.DOTALL),
            msg="开发模式证据必须在会清理 artifacts 的最终构建之后生成",
        )
        self.assertRegex(workflow, re.compile(r"Upload.*?dev.*?smoke.*?actions/upload-artifact@v4", re.DOTALL | re.IGNORECASE))
        self.assertIn("$devEvidence = Get-Content", workflow)
        self.assertIn("$evidence.installer_name", workflow)
        self.assertIn("$evidence.installer_sha512", workflow)
        release_assets = workflow[workflow.index("$assets = @(") :]
        self.assertIn("dev-smoke-evidence.json", release_assets)
        self.assertRegex(workflow, re.compile(r"开发模式.*?\$devEvidence", re.DOTALL))

    def test_release_workflow_normalizes_windows_temp_paths_and_python_encoding(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")

        self.assertIn("$env:RUNNER_TEMP", workflow)
        self.assertIn('"TEMP=$normalizedTemp" >> $env:GITHUB_ENV', workflow)
        self.assertIn('"TMP=$normalizedTemp" >> $env:GITHUB_ENV', workflow)
        self.assertIn("PYTHONUTF8: '1'", workflow)
        self.assertIn("PYTHONIOENCODING: utf-8", workflow)

    def test_delivery_docs_separate_verified_facts_from_release_gates(self) -> None:
        acceptance = (ROOT / "docs" / "CD-8客户端封装验收记录.md").read_text(encoding="utf-8")
        troubleshooting = (ROOT / "docs" / "客户端故障排查说明.md").read_text(encoding="utf-8")
        release_checklist = (ROOT / "docs" / "客户端发布检查表.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        for required in ("自动化通过", "人工验收", "环境性跳过", "尚未完成", "未签名预发布候选"):
            self.assertIn(required, acceptance)
        self.assertIn("以脚本运行时输出为准", acceptance)
        self.assertIn("dev-smoke-evidence.json", acceptance)
        self.assertIn("runtime_mode: development", acceptance)
        self.assertIn("dev-smoke-evidence.json", release_checklist)
        self.assertNotRegex(acceptance, r"SHA-512\s*[:：]\s*[A-Fa-f0-9]{128}")
        self.assertIn("日志", troubleshooting)
        self.assertIn("备份", troubleshooting)
        self.assertIn("不要删除", troubleshooting)
        self.assertIn("CD-8", readme)
        self.assertIn("无需手工启动", readme)

    def test_full_check_script_fails_fast_on_native_command_errors(self) -> None:
        script = (ROOT / "scripts" / "run_checks.ps1").read_text(encoding="utf-8")

        self.assertIn("function Invoke-CheckedCommand", script)
        self.assertIn("$LASTEXITCODE", script)
        self.assertIn("throw", script)
        for command in ("-m unittest discover", "-m compileall", "npm run build", "npm audit --audit-level=high"):
            self.assertIn(command, script)


if __name__ == "__main__":
    unittest.main()
