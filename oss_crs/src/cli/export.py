# SPDX-License-Identifier: MIT
"""Export prepared Docker images and CRS source trees into a tarball.

The bundle contains an export manifest, an optional ``images.tar`` produced by
``docker save``, and each configured CRS source tree under ``crs_src/``. Docker
images are optional because a CRS may not produce any prepare-phase images.
"""

import json
import platform
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from .clean import discover_prepare_images


def _save_images(image_tags: list[str], dest: Path) -> bool:
    """Save Docker images to *dest*."""
    print(f"Saving {len(image_tags)} image(s) with 'docker save'...")
    proc = subprocess.run(["docker", "save", "-o", str(dest), *image_tags])
    if proc.returncode != 0:
        print("Error: 'docker save' failed.", file=sys.stderr)
        return False
    return True


def handle_export(args, crs_compose) -> bool:
    """Export locally prepared images and CRS sources."""
    out_path = Path(args.out)
    image_tags = discover_prepare_images(crs_compose)
    if not image_tags:
        print(
            "Warning: no prepared images found locally "
            "(the CRS may have no prepare phase, or 'prepare' was not run); "
            "exporting CRS source only."
        )

    crs_sources: list[tuple[str, Path]] = []
    for crs in crs_compose.crs_list:
        src = Path(crs.crs_path)
        if src.exists():
            crs_sources.append((crs.name, src))
        else:
            print(f"No source found for CRS '{crs.name}' at {src}; skipping.")

    if not image_tags and not crs_sources:
        print(
            "Error: nothing to export (no prepared images or CRS source found).",
            file=sys.stderr,
        )
        return False

    manifest = {
        "oss_crs_export_version": 1,
        "platform": platform.machine(),
        "images": image_tags,
        "crs_sources": [name for name, _ in crs_sources],
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as staging_str:
        staging = Path(staging_str)
        images_tar: Path | None = None
        if image_tags:
            images_tar = staging / "images.tar"
            if not _save_images(image_tags, images_tar):
                return False

        manifest_json = staging / "export-manifest.json"
        manifest_json.write_text(json.dumps(manifest, indent=2))

        print(f"Writing bundle to {out_path}...")
        with tarfile.open(out_path, "w") as tar:
            tar.add(manifest_json, arcname="export-manifest.json")
            if images_tar is not None:
                tar.add(images_tar, arcname="images.tar")
            for name, src in crs_sources:
                tar.add(src, arcname=f"crs_src/{name}")

    summary = []
    if image_tags:
        summary.append(f"{len(image_tags)} image(s)")
    if crs_sources:
        summary.append(f"{len(crs_sources)} CRS source(s)")
    print(f"Exported {' + '.join(summary)} to {out_path}")
    return True
