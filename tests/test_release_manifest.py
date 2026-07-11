from __future__ import annotations

import base64
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


class ReleaseManifestTests(unittest.TestCase):
    def test_workflow_only_releases_from_semver_tags_or_manual_dispatch_with_write_permission(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertRegex(workflow, r"tags:\s*\n\s*- ['\"]v\*['\"]")
        self.assertIn("contents: write", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotRegex(workflow, r"(?m)^\s{2}branches:")

    def test_workflow_forces_prerelease_without_both_signing_secrets_and_verifies_stable_signature(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
        for value in ("CSC_LINK", "CSC_KEY_PASSWORD", "StableAllowed", "Get-AuthenticodeSignature", "Valid", "--prerelease"):
            self.assertIn(value, workflow)
        self.assertRegex(workflow, r"CSC_LINK.+-and.+CSC_KEY_PASSWORD")
        self.assertRegex(workflow, r"\^\\d\+\\\.\\d\+\\\.\\d\+\$")

    def test_workflow_passes_context_through_env_and_scopes_signing_secrets_to_build_step(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
        run_blocks = [section.split("run: |", 1)[1] for section in workflow.split("\n      - name:") if "run: |" in section]
        self.assertTrue(run_blocks)
        self.assertTrue(all("${{" not in block for block in run_blocks), "PowerShell 源码不得直接插入 GitHub 表达式")
        self.assertIn("RELEASE_INPUT_VERSION: ${{ inputs.version }}", workflow)
        self.assertNotRegex(workflow, r"(?ms)^\s{4}env:\s*\n\s+CSC_LINK:")
        build = re.search(r"(?ms)- name: Build NSIS.*?(?=\n\s+- name:)", workflow)
        self.assertIsNotNone(build)
        assert build is not None
        self.assertIn("CSC_LINK: ${{ secrets.WINDOWS_CSC_LINK }}", build.group(0))
        self.assertIn("SetEnvironmentVariable('CSC_LINK', $null, 'Process')", build.group(0))
        self.assertIn("SetEnvironmentVariable('CSC_KEY_PASSWORD', $null, 'Process')", build.group(0))
        self.assertIn("$env:CSC_IDENTITY_AUTO_DISCOVERY='false'", build.group(0))

    def test_push_tag_is_single_v_semver_and_manual_release_targets_checked_out_commit(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
        self.assertNotIn("TrimStart", workflow)
        self.assertIn("^v\\d+\\.\\d+\\.\\d+", workflow)
        self.assertIn("RELEASE_COMMIT: ${{ github.sha }}", workflow)
        self.assertIn("--target", workflow)
        self.assertIn("$env:RELEASE_EVENT_NAME -ne 'push'", workflow)

    def test_stable_signature_covers_frontend_and_packaged_backend_executables(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
        self.assertIn("win-unpacked/FuLuA.exe", workflow)
        self.assertIn("win-unpacked/resources/backend/fulua-backend.exe", workflow)

    def test_release_is_immutable_and_generates_dynamic_evidence_report(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
        self.assertNotIn("upload --clobber", workflow)
        self.assertNotIn("gh release upload", workflow)
        self.assertIn("Release already exists", workflow)
        self.assertIn("git ls-remote", workflow)
        self.assertNotIn("git ls-remote --exit-code", workflow)
        self.assertIn("$tagRefs.Count -gt 0", workflow)
        for evidence in ("RELEASE_COMMIT: ${{ github.sha }}", "test_status", "signature_status", "SHA256SUMS.txt", "发布检查报告.md"):
            self.assertIn(evidence, workflow)
        self.assertEqual(
            workflow.lower().count("signature_status:"),
            1,
            "GitHub Actions 的 env 键不区分大小写，重复签名状态键会导致 workflow 无法解析",
        )

    def test_release_artifact_allowlist_is_complete(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
        for artifact in ("*setup*.exe", "latest.yml", "*.blockmap", "SHA256SUMS.txt", "发布检查报告.md"):
            self.assertIn(artifact, workflow)
        self.assertIn("verify_release_manifest.py", workflow)

    def test_builder_declares_stable_github_provider_and_update_compatibility(self) -> None:
        config = (ROOT / "desktop" / "electron-builder.yml").read_text(encoding="utf-8")
        for expected in ("provider: github", "owner: ZN4819", "repo: Open-FuLuA", "channel: latest", 'electronUpdaterCompatibility: \">=2.16\"'):
            self.assertIn(expected, config)
        self.assertIn('artifactName: "fulua-desktop-setup-${version}.${ext}"', config)

    def test_latest_yml_verifier_accepts_matching_setup_sha512_and_rejects_mismatch(self) -> None:
        script_path = ROOT / "scripts" / "verify_release_manifest.py"
        spec = importlib.util.spec_from_file_location("release_manifest", script_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            setup = root / "FuLuA Setup 0.2.0.exe"
            setup.write_bytes(b"signed-or-prerelease-installer")
            digest = base64.b64encode(hashlib.sha512(setup.read_bytes()).digest()).decode("ascii")
            latest = root / "latest.yml"
            latest.write_text(
                f"version: 0.2.0\nfiles:\n  - url: FuLuA Setup 0.2.0.exe\n    sha512: {digest}\n    size: {setup.stat().st_size}\npath: FuLuA Setup 0.2.0.exe\nsha512: {digest}\n",
                encoding="utf-8",
            )
            result = module.verify_latest_yml(latest, root)
            self.assertEqual(result["version"], "0.2.0")
            latest.write_text(latest.read_text(encoding="utf-8").replace(digest, "AAAA"), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "SHA-512"):
                module.verify_latest_yml(latest, root)

    def test_latest_yml_requires_one_consistent_setup_entry_with_matching_size_and_hash(self) -> None:
        script_path = ROOT / "scripts" / "verify_release_manifest.py"
        spec = importlib.util.spec_from_file_location("release_manifest_strict", script_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory); setup = root / "FuLuA Setup 0.2.0.exe"
            setup.write_bytes(b"installer")
            digest = base64.b64encode(hashlib.sha512(setup.read_bytes()).digest()).decode("ascii")
            base = f"version: 0.2.0\nfiles:\n  - url: {setup.name}\n    sha512: {{file_hash}}\n    size: {{size}}\npath: {setup.name}\nsha512: {digest}\n"
            for text, message in [
                (base.format(file_hash="AAAA", size=setup.stat().st_size), "files|SHA-512"),
                (base.format(file_hash=digest, size=setup.stat().st_size + 1), "size|大小"),
                (base.format(file_hash=digest, size=setup.stat().st_size).replace(
                    f"path: {setup.name}", f"  - url: Other Setup.exe\n    sha512: {digest}\n    size: {setup.stat().st_size}\npath: {setup.name}"
                ), "唯一|Setup"),
            ]:
                latest = root / "latest.yml"; latest.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    module.verify_latest_yml(latest, root)

    def test_release_manifest_declares_direct_pyyaml_dependency_and_docs_match_dynamic_evidence(self) -> None:
        requirements = (ROOT / "backend" / "requirements-packaging.txt").read_text(encoding="utf-8")
        self.assertRegex(requirements, r"(?mi)^PyYAML==")
        checklist = (ROOT / "docs" / "客户端发布检查表.md").read_text(encoding="utf-8")
        self.assertIn("resources/backend/fulua-backend.exe", checklist)
        self.assertIn("动态", checklist)
        self.assertNotIn("和本检查表", checklist)

    def test_upgrade_guide_does_not_claim_editing_can_continue_after_unsafe_sidecar_failure(self) -> None:
        guide = (ROOT / "docs" / "客户端升级与恢复说明.md").read_text(encoding="utf-8")
        self.assertIn("失败不会开始安装", guide)
        self.assertIn("不可继续编辑", guide)


if __name__ == "__main__":
    unittest.main()
