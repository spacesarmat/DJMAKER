from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = PROJECT_ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Не удалось загрузить {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EssentiaBuildToolsTests(unittest.TestCase):
    def test_mingw_patch_is_strict_and_switches_to_x64(self) -> None:
        module = load_script("patch_essentia_mingw64.py")
        source = (
            "\"\"\"historical block\n"
            "ctx.env.CXXFLAGS = ['-static-libgcc', '-static-libstdc++']\n"
            "\"\"\"\n"
            "ctx.find_program('i686-w64-mingw32-gcc', var='CC')\n"
            "ctx.find_program('i686-w64-mingw32-g++', var='CXX')\n"
            "ctx.find_program('i686-w64-mingw32-ar', var='AR')\n"
            "ctx.env.CXXFLAGS = ['-static-libgcc', '-static-libstdc++']\n"
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "wscript"
            path.write_text(source, encoding="utf-8")
            module.patch_wscript(path)
            patched = path.read_text(encoding="utf-8")

        self.assertNotIn("i686-w64-mingw32", patched)
        self.assertEqual(3, patched.count("x86_64-w64-mingw32"))
        self.assertEqual(1, patched.count("ctx.env.CXXFLAGS +="))
        self.assertIn("-D_USE_MATH_DEFINES", patched)
        self.assertEqual(1, patched.count("ctx.env.CXXFLAGS = ['-static-libgcc'"))

    def test_workflow_pins_compatible_eigen_for_all_platforms(self) -> None:
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "build-essentia-runtime.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("ESSENTIA_RUNTIME_VERSION: 2026.08.27-66a890f2-r6", workflow)
        self.assertIn(
            "EIGEN_COMMIT: 3147391d946bb4b6c68edd901f2add6ac1f31f8c",
            workflow,
        )
        self.assertIn("EIGEN_VERSION: 3.4.0", workflow)
        self.assertNotIn("brew install eigen", workflow)
        self.assertNotIn("libeigen3-dev", workflow)
        self.assertGreaterEqual(workflow.count("-Ivendor/eigen"), 2)
        self.assertGreaterEqual(
            workflow.count("unsupported/Eigen/CXX11/Tensor"),
            2,
        )
        self.assertIn("-D_USE_MATH_DEFINES", workflow)
        self.assertGreaterEqual(workflow.count("-DEIGEN_MPL2_ONLY"), 2)

    def test_windows_build_uses_cxx14_to_avoid_mingw_std_byte_conflict(self) -> None:
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "build-essentia-runtime.yml"
        ).read_text(encoding="utf-8")
        windows_block = workflow.split("  test-windows-amd64:", 1)[0]
        macos_block = workflow.split("  macos:", 1)[1].split("  publish:", 1)[0]

        self.assertIn("--std=c++14", windows_block)
        self.assertIn("-std=c++14", windows_block)
        self.assertNotIn("c++17", windows_block)
        self.assertIn("--std=c++17", macos_block)
        self.assertIn("-std=c++17", macos_block)

    def test_workflow_uses_node24_actions(self) -> None:
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "build-essentia-runtime.yml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("actions/checkout@v4", workflow)
        self.assertIn("actions/checkout@v7", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertIn("actions/download-artifact@v8", workflow)

    def test_eigen_pkgconfig_writer_uses_pinned_header_root(self) -> None:
        module = load_script("write_eigen_pkgconfig.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            eigen = root / "eigen"
            (eigen / "Eigen").mkdir(parents=True)
            (eigen / "unsupported" / "Eigen" / "CXX11").mkdir(parents=True)
            (eigen / "Eigen" / "Core").write_text("// core", encoding="utf-8")
            (eigen / "unsupported" / "Eigen" / "CXX11" / "Tensor").write_text(
                "// tensor",
                encoding="utf-8",
            )
            output = root / "pkgconfig" / "eigen3.pc"
            module.write_eigen_pkgconfig(eigen, output, "3.4.0")
            content = output.read_text(encoding="utf-8")

        self.assertIn("Version: 3.4.0", content)
        self.assertIn("Cflags: -I${includedir}", content)
        self.assertIn(eigen.resolve().as_posix(), content)

    def test_runtime_packager_writes_manifest_and_source(self) -> None:
        module = load_script("package_essentia_runtime.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "djmaker-essentia.exe"
            license_file = root / "COPYING.txt"
            source_file = root / "analyzer.cpp"
            eigen_license = root / "COPYING.MPL2"
            output = root / "runtime.zip"
            binary.write_bytes(b"binary")
            license_file.write_text("AGPL", encoding="utf-8")
            source_file.write_text("// source", encoding="utf-8")
            eigen_license.write_text("MPL2", encoding="utf-8")

            module.make_archive(
                binary=binary,
                output=output,
                platform_name="windows",
                arch="amd64",
                version="test-r1",
                upstream_sha="a" * 40,
                license_file=license_file,
                analyzer_source=source_file,
                eigen_license_file=eigen_license,
            )

            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                manifest = json.loads(
                    archive.read("djmaker-essentia/manifest.json").decode("utf-8")
                )

        self.assertIn("djmaker-essentia/bin/djmaker-essentia.exe", names)
        self.assertIn("djmaker-essentia/COPYING-ESSENTIA.txt", names)
        self.assertIn("djmaker-essentia/COPYING-EIGEN-MPL2.txt", names)
        self.assertIn("djmaker-essentia/djmaker_essentia_analyzer.cpp", names)
        self.assertEqual("amd64", manifest["arch"])
        self.assertEqual("test-r1", manifest["runtime_version"])

    def test_bridge_emits_full_beat_grid_payload(self) -> None:
        source = (
            PROJECT_ROOT / "tools" / "essentia" / "djmaker_essentia_analyzer.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn('\\"beat_ticks\\"', source)
        self.assertIn('\\"first_downbeat\\"', source)
        self.assertIn('\\"tempo_stability\\"', source)
        self.assertIn('\\"downbeat_confidence\\"', source)
        self.assertIn("estimate_downbeat", source)


if __name__ == "__main__":
    unittest.main()
