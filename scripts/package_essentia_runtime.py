"""Упаковка собственного Essentia runtime DJMAKER без сторонних Python-пакетов."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
import tempfile
import zipfile
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(
    path: Path,
    *,
    platform_name: str,
    arch: str,
    version: str,
    upstream_sha: str,
    binary_name: str,
    binary_hash: str,
) -> None:
    payload = {
        "schema": 1,
        "name": "DJMAKER Essentia Runtime",
        "runtime_version": version,
        "essentia_upstream_sha": upstream_sha,
        "platform": platform_name,
        "arch": arch,
        "binary": f"bin/{binary_name}",
        "binary_sha256": binary_hash,
        "audio_input": "mono f32le PCM, 44100 Hz",
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def make_archive(
    *,
    binary: Path,
    output: Path,
    platform_name: str,
    arch: str,
    version: str,
    upstream_sha: str,
    license_file: Path,
    analyzer_source: Path,
) -> None:
    if not binary.is_file():
        raise FileNotFoundError(binary)
    if not license_file.is_file():
        raise FileNotFoundError(license_file)
    if not analyzer_source.is_file():
        raise FileNotFoundError(analyzer_source)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_name:
        root = Path(temp_name) / "djmaker-essentia"
        bin_dir = root / "bin"
        bin_dir.mkdir(parents=True)
        target = bin_dir / binary.name
        target.write_bytes(binary.read_bytes())
        if platform_name == "darwin":
            target.chmod(target.stat().st_mode | 0o755)

        (root / "COPYING-ESSENTIA.txt").write_bytes(license_file.read_bytes())
        (root / "djmaker_essentia_analyzer.cpp").write_bytes(analyzer_source.read_bytes())
        write_manifest(
            root / "manifest.json",
            platform_name=platform_name,
            arch=arch,
            version=version,
            upstream_sha=upstream_sha,
            binary_name=target.name,
            binary_hash=sha256(target),
        )

        if output.name.endswith(".zip"):
            with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(root.parent).as_posix())
            return

        if output.name.endswith((".tar.gz", ".tgz")):
            with tarfile.open(output, "w:gz") as archive:
                archive.add(root, arcname=root.name)
            return

        raise ValueError(f"Неподдерживаемый формат архива: {output}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--platform", choices=("windows", "darwin"), required=True)
    parser.add_argument("--arch", choices=("amd64", "arm64"), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--upstream-sha", required=True)
    parser.add_argument("--license", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()

    make_archive(
        binary=args.binary,
        output=args.output,
        platform_name=args.platform,
        arch=args.arch,
        version=args.version,
        upstream_sha=args.upstream_sha,
        license_file=args.license,
        analyzer_source=args.source,
    )
    print(f"{sha256(args.output)}  {args.output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
