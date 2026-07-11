from __future__ import annotations

import base64
import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseManifestTests(unittest.TestCase):
    def test_workflow_only_releases_from_semver_tags_or_manual_dispatch_with_write_permission(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertRegex(workflow, r"tags:\s*\n\s*- ['\"]v\[0-9\]")
        self.assertIn("contents: write", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotRegex(workflow, r"(?m)^\s{2}branches:")

    def test_workflow_forces_prerelease_without_both_signing_secrets_and_verifies_stable_signature(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
        for value in ("CSC_LINK", "CSC_KEY_PASSWORD", "StableAllowed", "Get-AuthenticodeSignature", "Valid", "--prerelease"):
            self.assertIn(value, workflow)
        self.assertRegex(workflow, r"CSC_LINK.+-and.+CSC_KEY_PASSWORD")

    def test_release_artifact_allowlist_is_complete(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "desktop-release.yml").read_text(encoding="utf-8")
        for artifact in ("*Setup*.exe", "latest.yml", "*.blockmap", "SHA256SUMS.txt", "发布检查表.md"):
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


if __name__ == "__main__":
    unittest.main()
