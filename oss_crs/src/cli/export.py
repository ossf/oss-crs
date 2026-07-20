# SPDX-License-Identifier: MIT
"""Export prepared Docker images and CRS source trees into a tarball.

The bundle contains an export manifest, an optional ``images.tar.zst``
produced by streaming ``docker save`` through ``zstd``, and each
configured CRS source tree under ``crs_src/``.  Docker images are
optional because a CRS may not produce any prepare-phase images.
"""

import json
import platform
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from .discovery import discover_prepare_images


def _stream_images(image_tags: list[str], dest: Path) -> bool:
    """Stream ``docker save`` through ``zstd`` into *dest*.

    The zstd compression eliminates the need for an uncompressed
    temporary ``images.tar`` file, reducing peak disk usage by roughly
    half.
    """
    print(f"Saving {len(image_tags)} image(s) with 'docker save | zstd'...")
    save = subprocess.Popen(
        ["docker", "save", *image_tags],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if save.stdout is None:
        print("Error: 'docker save' produced no output pipe.", file=sys.stderr)
        return False
    zstd = subprocess.Popen(
        ["zstd", "-T0", "-10", "-o", str(dest), "-q"],
        stdin=save.stdout,
        stderr=subprocess.PIPE,
    )
    save.stdout.close()

    # Wait for both processes. If one fails the other may receive SIGPIPE
    # or EOF, so we collect all available error information before deciding
    # what went wrong.
    save_rc = save.wait()
    zstd_rc = zstd.wait()

    save_error = (
        save.stderr.read().decode(errors="replace").strip() if save.stderr else ""
    )
    zstd_error = (
        zstd.stderr.read().decode(errors="replace").strip() if zstd.stderr else ""
    )

    # Collect all failures — the root cause might be in either process.
    errors: list[str] = []

    if save_rc != 0 and save_error:
        errors.append(save_error)

    if zstd_rc != 0 and zstd_error:
        errors.append(zstd_error)

    if errors:
        print("Error: image export failed.\n", file=sys.stderr)
        for err in errors:
            print(err, file=sys.stderr)
            print(file=sys.stderr)
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
        "compression": "zstd",
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as staging_str:
        staging = Path(staging_str)

        images_tar_zst: Path | None = None
        if image_tags:
            images_tar_zst = staging / "images.tar.zst"
            if not _stream_images(image_tags, images_tar_zst):
                return False

        manifest_json = staging / "export-manifest.json"
        manifest_json.write_text(json.dumps(manifest, indent=2))

        print(f"Writing bundle to {out_path}...")
        with tarfile.open(out_path, "w", format=tarfile.GNU_FORMAT) as tar:
            tar.add(manifest_json, arcname="export-manifest.json")
            if images_tar_zst is not None:
                tar.add(images_tar_zst, arcname="images.tar.zst")
            for name, src in crs_sources:
                tar.add(src, arcname=f"crs_src/{name}")

    summary = []
    if image_tags:
        summary.append(f"{len(image_tags)} image(s)")
    if crs_sources:
        summary.append(f"{len(crs_sources)} CRS source(s)")
    print(f"Exported {' + '.join(summary)} to {out_path}")
    return True
