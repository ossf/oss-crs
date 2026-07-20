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

from ..config.crs_compose import CRSComposeConfig
from ..utils import rm_with_docker
from ..workdir import WorkDir


def _load_images(images_tar: Path) -> bool:
    """Load bundled images into the local Docker daemon."""
    print("Loading images with 'docker load'...")
    proc = subprocess.run(["docker", "load", "-i", str(images_tar)])
    if proc.returncode != 0:
        print("Error: 'docker load' failed.", file=sys.stderr)
        return False
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

    config = CRSComposeConfig.from_yaml_file(args.compose_file)
    work_dir = WorkDir(args.work_dir / f"crs_compose/{config.md5_hash()}")
    crs_src_base = work_dir.path.parent / "crs_src"

    staging_parent = work_dir.path.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=staging_parent, prefix=".import-"))
    try:
        print(f"Extracting bundle {bundle}...")
        with tarfile.open(bundle, "r") as tar:
            tar.extractall(staging, filter="fully_trusted")

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

        images_tar = staging / "images.tar"
        if images_tar.exists():
            if not _load_images(images_tar):
                return False
        else:
            print("No images in bundle; skipping 'docker load'.")

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
        rm_with_docker(staging)

    summary = []
    if images:
        summary.append(f"{len(images)} image(s)")
    if restored_sources:
        summary.append(f"{len(restored_sources)} CRS source(s)")
    restored = " + ".join(summary) if summary else "nothing"
    print(f"Imported {restored} into {work_dir.path}")
    return True
