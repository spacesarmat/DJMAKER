from __future__ import annotations

import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from djmaker.runtime.dependencies import (
    ESSENTIA_RUNTIME_VERSION,
    ESSENTIA_UPSTREAM_SHA,
    RuntimeDependencies,
    RuntimeDependencyError,
    _checksum_for_asset,
    _essentia_asset_name,
    _extract_archive_safely,
    _ffmpeg_asset_name,
    _normalized_machine,
    _parse_essentia_version,
)


class RuntimeDependencyHelpersTests(unittest.TestCase):
    def test_normalizes_common_architectures(self) -> None:
        self.assertEqual("amd64", _normalized_machine("AMD64"))
        self.assertEqual("amd64", _normalized_machine("x86_64"))
        self.assertEqual("arm64", _normalized_machine("aarch64"))
        self.assertEqual("arm64", _normalized_machine("ARM64"))

    def test_ffmpeg_asset_is_selected_for_windows_and_macos(self) -> None:
        self.assertEqual(
            "ffmpeg-windows-amd64.zip",
            _ffmpeg_asset_name("Windows", "x86_64"),
        )
        self.assertEqual(
            "ffmpeg-darwin-arm64.tar.gz",
            _ffmpeg_asset_name("Darwin", "arm64"),
        )
        self.assertIsNone(_ffmpeg_asset_name("Windows", "i686"))

    def test_own_essentia_assets_are_selected_for_supported_targets(self) -> None:
        self.assertEqual(
            "djmaker-essentia-windows-amd64.zip",
            _essentia_asset_name("Windows", "AMD64"),
        )
        self.assertEqual(
            "djmaker-essentia-darwin-amd64.tar.gz",
            _essentia_asset_name("Darwin", "x86_64"),
        )
        self.assertEqual(
            "djmaker-essentia-darwin-arm64.tar.gz",
            _essentia_asset_name("Darwin", "arm64"),
        )
        self.assertIsNone(_essentia_asset_name("Windows", "arm64"))
        self.assertIsNone(_essentia_asset_name("Linux", "x86_64"))

    def test_essentia_runtime_revision_matches_build_recipe(self) -> None:
        self.assertEqual(
            "2026.08.27-66a890f2-r6",
            ESSENTIA_RUNTIME_VERSION,
        )

    def test_essentia_runtime_is_pinned_to_exact_upstream_commit(self) -> None:
        self.assertEqual(40, len(ESSENTIA_UPSTREAM_SHA))
        self.assertRegex(ESSENTIA_UPSTREAM_SHA, r"^[0-9a-f]{40}$")
        self.assertIn(ESSENTIA_UPSTREAM_SHA[:8], ESSENTIA_RUNTIME_VERSION)

    def test_essentia_version_parser(self) -> None:
        self.assertEqual(
            ESSENTIA_RUNTIME_VERSION,
            _parse_essentia_version(
                f"djmaker-essentia {ESSENTIA_RUNTIME_VERSION} upstream=abc"
            ),
        )

    def test_checksum_parser_accepts_standard_sha256_format(self) -> None:
        digest = "a" * 64
        checksums = f"{digest}  djmaker-essentia-windows-amd64.zip\n"
        self.assertEqual(
            digest,
            _checksum_for_asset(checksums, "djmaker-essentia-windows-amd64.zip"),
        )

    def test_zip_path_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "unsafe.zip"
            destination = root / "output"
            destination.mkdir()
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../escape.exe", b"bad")

            with self.assertRaises(RuntimeDependencyError):
                _extract_archive_safely(archive, destination)


class RuntimeDependenciesTests(unittest.TestCase):
    def test_managed_essentia_runtime_has_priority_over_python_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = RuntimeDependencies(Path(temp))
            binary = runtime._managed_essentia_path()
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"placeholder")

            with (
                patch(
                    "djmaker.runtime.dependencies._run_version_command",
                    return_value=f"djmaker-essentia {ESSENTIA_RUNTIME_VERSION} upstream=x",
                ),
                patch.object(runtime, "_import_essentia_version", return_value="python-version"),
            ):
                status = runtime._probe_essentia()

            self.assertTrue(status.available)
            self.assertTrue(status.managed)
            self.assertEqual("djmaker-cli", status.backend)
            self.assertEqual(ESSENTIA_RUNTIME_VERSION, status.version)

    def test_python_essentia_is_only_a_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = RuntimeDependencies(Path(temp))
            with patch.object(runtime, "_import_essentia_version", return_value="2.1-test"):
                status = runtime._probe_essentia()

            self.assertTrue(status.available)
            self.assertEqual("python-fallback", status.backend)

    def test_stale_managed_runtime_is_rejected_for_grid_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = RuntimeDependencies(Path(temp))
            binary = runtime._managed_essentia_path()
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"placeholder")

            with patch(
                "djmaker.runtime.dependencies._run_version_command",
                return_value="djmaker-essentia 2026.08.27-66a890f2-r5 upstream=x",
            ):
                status = runtime._probe_managed_essentia()

            self.assertFalse(status.available)
            self.assertIn("r5", status.detail)
            self.assertIn("r6", status.detail)
            self.assertIsNone(runtime.essentia_analyzer_path())


class AstModelDependencyTests(unittest.TestCase):
    def _fake_assets(self) -> tuple[tuple[str, str, int], ...]:
        payloads = {
            "onnx/model_fp16.onnx": b"fake-onnx-model",
            "config.json": b'{"id2label": {"220": "Heavy metal"}}',
            "preprocessor_config.json": b'{"mean": -4.2677393}',
        }
        self._payloads = payloads
        return tuple(
            (name, hashlib.sha256(data).hexdigest(), len(data))
            for name, data in payloads.items()
        )

    def test_probe_reports_unavailable_when_files_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = RuntimeDependencies(Path(temp))
            status = runtime._probe_ast()

            self.assertFalse(status.available)
            self.assertIsNone(runtime.ast_model_path())

    def test_ensure_ast_model_downloads_and_verifies_all_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = RuntimeDependencies(Path(temp))
            fake_assets = self._fake_assets()

            def fake_download(url: str, destination: Path) -> None:
                name = url.rsplit("/", 1)[-1]
                for asset_name, _hash, _size in fake_assets:
                    if asset_name.endswith(name):
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(self._payloads[asset_name])
                        return
                raise AssertionError(f"unexpected download url: {url}")

            with (
                patch("djmaker.runtime.dependencies.AST_ASSETS", fake_assets),
                patch("djmaker.runtime.dependencies._download_file", side_effect=fake_download),
            ):
                status = runtime.ensure_ast_model()

                self.assertTrue(status.available)
                self.assertTrue(status.managed)
                self.assertIsNotNone(runtime.ast_model_path())
                self.assertIsNotNone(runtime.ast_config_path())
                self.assertIsNotNone(runtime.ast_preprocessor_config_path())

    def test_ensure_ast_model_rejects_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = RuntimeDependencies(Path(temp))
            fake_assets = self._fake_assets()

            def fake_download_corrupted(url: str, destination: Path) -> None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"corrupted-payload")

            with (
                patch("djmaker.runtime.dependencies.AST_ASSETS", fake_assets),
                patch(
                    "djmaker.runtime.dependencies._download_file",
                    side_effect=fake_download_corrupted,
                ),
            ):
                status = runtime.ensure_ast_model()

            self.assertFalse(status.available)
            self.assertIn("Ошибка автоустановки", status.detail)
            self.assertIsNone(runtime.ast_model_path())

    def test_ensure_ast_model_is_idempotent_when_already_installed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = RuntimeDependencies(Path(temp))
            fake_assets = self._fake_assets()
            runtime.ast_dir.mkdir(parents=True)
            for name, _hash, _size in fake_assets:
                (runtime.ast_dir / Path(name).name).write_bytes(self._payloads[name])

            with (
                patch("djmaker.runtime.dependencies.AST_ASSETS", fake_assets),
                patch("djmaker.runtime.dependencies._download_file") as download,
            ):
                status = runtime.ensure_ast_model()

            download.assert_not_called()
            self.assertTrue(status.available)


if __name__ == "__main__":
    unittest.main()
