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

    def test_windows_workflow_enables_mingw_math_constants(self) -> None:
        workflow = (
            PROJECT_ROOT / ".github" / "workflows" / "build-essentia-runtime.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("ESSENTIA_RUNTIME_VERSION: 2026.08.27-66a890f2-r3", workflow)
        self.assertIn("-D_USE_MATH_DEFINES", workflow)
        self.assertGreaterEqual(workflow.count("pkg-config --cflags eigen3"), 2)
        self.assertGreaterEqual(workflow.count("unsupported/Eigen/CXX11/Tensor"), 2)
        self.assertGreaterEqual(workflow.count("-DEIGEN_MPL2_ONLY"), 2)

    def test_runtime_packager_writes_manifest_and_source(self) -> None:
        module = load_script("package_essentia_runtime.py")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            binary = root / "djmaker-essentia.exe"
            license_file = root / "COPYING.txt"
            source_file = root / "analyzer.cpp"
            output = root / "runtime.zip"
            binary.write_bytes(b"binary")
            license_file.write_text("AGPL", encoding="utf-8")
            source_file.write_text("// source", encoding="utf-8")

            module.make_archive(
                binary=binary,
                output=output,
                platform_name="windows",
                arch="amd64",
                version="test-r1",
                upstream_sha="a" * 40,
                license_file=license_file,
                analyzer_source=source_file,
            )

            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                manifest = json.loads(
                    archive.read("djmaker-essentia/manifest.json").decode("utf-8")
                )

        self.assertIn("djmaker-essentia/bin/djmaker-essentia.exe", names)
        self.assertIn("djmaker-essentia/COPYING-ESSENTIA.txt", names)
        self.assertIn("djmaker-essentia/djmaker_essentia_analyzer.cpp", names)
        self.assertEqual("amd64", manifest["arch"])
        self.assertEqual("test-r1", manifest["runtime_version"])


if __name__ == "__main__":
    unittest.main()
