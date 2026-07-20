# SPDX-License-Identifier: MIT
"""Import Docker images and CRS source from an ``oss-crs export`` bundle."""

import json
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from ..utils import rm_with_docker


def _load_images(staging: Path) -> bool:
    """Load bundled images into the local Docker daemon.

    Streams ``zstd -d`` directly into ``docker load`` with no
    intermediate uncompressed file.
    """
    images_tar_zst = staging / "images.tar.zst"

    if not images_tar_zst.exists():
        print("No images in bundle; skipping 'docker load'.")
        return True

    print("Loading images with 'zstd -d | docker load'...")
    decompress = subprocess.Popen(
        ["zstd", "-d", "-c", str(images_tar_zst)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if decompress.stdout is None:
        print(
            "Error: 'zstd -d' produced no output pipe.",
            file=sys.stderr,
        )
        return False

    load = subprocess.Popen(
        ["docker", "load"],
        stdin=decompress.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    decompress.stdout.close()
    dc_rc = decompress.wait()
    ld_rc = load.wait()

    dc_err = (
        decompress.stderr.read().decode(errors="replace").strip()
        if decompress.stderr
        else ""
    )
    load_out = (
        load.stdout.read().decode(errors="replace").strip() if load.stdout else ""
    )
    load_err = (
        load.stderr.read().decode(errors="replace").strip() if load.stderr else ""
    )

    if dc_rc != 0 and dc_err:
        print(dc_err, file=sys.stderr)
        return False

    if ld_rc != 0:
        print(load_err, file=sys.stderr)
        return False

    if load_out:
        print(load_out)

    return True


def _restore_dir(src: Path, dest: Path, *, label: str) -> None:
    """Move *src* onto *dest*, replacing an existing directory."""
    if dest.exists():
        print(f"Replacing existing {label} at {dest}.")
        rm_with_docker(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))


def handle_import(args) -> bool:
    """Import a bundle without cloning its CRS repositories."""
    bundle = Path(args.in_path)
    if not bundle.exists():
        print(f"Error: bundle not found: {bundle}", file=sys.stderr)
        return False

    crs_compose_dir = args.work_dir / "crs_compose"
    crs_src_base = crs_compose_dir / "crs_src"

    staging_parent = crs_compose_dir
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=staging_parent, prefix=".import-"))
    try:
        print(f"Extracting bundle {bundle}...")
        with tarfile.open(bundle, "r") as tar:
            tar.extractall(staging, filter="data")

        manifest_file = staging / "export-manifest.json"
        if not manifest_file.exists():
            print(
                "Error: bundle is missing export-manifest.json "
                "(not produced by 'export'?).",
                file=sys.stderr,
            )
            return False
        manifest = json.loads(manifest_file.read_text())

        images = manifest.get("images") or []
        bundle_platform = manifest.get("platform")
        host_platform = platform.machine()
        if images and bundle_platform and bundle_platform != host_platform:
            print(
                f"Error: bundle was built for '{bundle_platform}' but this host "
                f"is '{host_platform}'. Docker images are architecture-specific "
                "and cannot be imported here.",
                file=sys.stderr,
            )
            return False

        if not _load_images(staging):
            return False

        restored_sources: list[str] = []
        src_crs_root = staging / "crs_src"
        if src_crs_root.is_dir():
            for child in sorted(src_crs_root.iterdir()):
                if not child.is_dir():
                    continue
                dest = crs_src_base / child.name
                _restore_dir(child, dest, label=f"CRS source '{child.name}'")
                restored_sources.append(child.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    summary = []
    if images:
        summary.append(f"{len(images)} image(s)")
    if restored_sources:
        summary.append(f"{len(restored_sources)} CRS source(s)")
    restored = " + ".join(summary) if summary else "nothing"
    print(f"Imported {restored} into {crs_compose_dir}")
    return True
