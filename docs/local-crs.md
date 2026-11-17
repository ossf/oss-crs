# Running a CRS from a local checkout

`oss-crs local` starts a CRS command directly on the host. The CRS checkout is
the current directory by default. The framework builds the OSS-Fuzz project
image, starts Docker builder and runner sidecars, then starts the command in
the CRS checkout with the standard target and libCRS environment variables.
The sidecars stop with the session.

```sh
oss-crs local start \
  --fuzz-proj-path /path/to/oss-fuzz/projects/libxml2 \
  --target-harness xml \
  --crs-path /path/to/my-crs \
  --command -- python3 run.py
```

Pass any arguments to the CRS after the command name:

```sh
oss-crs local start \
  --fuzz-proj-path /path/to/oss-fuzz/projects/libxml2 \
  --crs-path /path/to/my-crs \
  --command -- python3 run.py --verbose
```

Omit `--target-harness` for a harness-generation or source-analysis run. Pass
`--target-source-path` when the target's source tree is already checked out
locally. Stop the matching session with the same CRS path, project path, and
harness:

```sh
oss-crs local stop \
  --fuzz-proj-path /path/to/oss-fuzz/projects/libxml2 \
  --target-harness xml \
  --crs-path /path/to/my-crs
```

Each session gets a separate directory under
`.oss-crs-workdir/local/<session>/` containing:

- `submit/`: files submitted through libCRS.
- `fetch/`: files available to libCRS fetch operations.
- `shared/` and `logs/`: shared data and persisted CRS logs.
- `build-out/` and `rebuild-out/`: output directories mounted into the
  runner and builder sidecars.
- `crs.log`: combined standard output and error from the CRS command.

The CRS receives the same libCRS command interface as a container run. The
fuzz-project and optional target-source variables resolve to host paths in this
mode, so `libCRS download-source` can read the checked-out trees directly.
The builder sidecar can rebuild the project from libCRS patch/build-project
calls, and the runner sidecar can reproduce PoVs. The work directories are
host-visible; a CRS can also launch its own Docker containers for dynamic tools
and mount the relevant session directories.

Docker must be available on the host. Local mode runs the CRS command on the
host and uses Docker for the OSS-Fuzz project image and OSS-CRS builder/runner
sidecars.
