# libCRS

A Python CLI library installed in every CRS container. It provides a uniform interface for CRS containers to interact with the OSS-CRS infrastructure - managing build outputs, exchanging data (seeds, PoVs, patches), sharing files between containers, communicating with builder sidecars, and resolving service endpoints.

## Installation

```dockerfile
COPY --from=libcrs . /opt/libCRS
RUN /opt/libCRS/install.sh
```

By default (no flags, or `--all`) this installs both the CLI and the Python library. Use `--cli` or `--python` to install only one:

```dockerfile
RUN /opt/libCRS/install.sh --cli     # CLI entry point only
RUN /opt/libCRS/install.sh --python  # importable Python library only
```

## Commands

| Category | Commands |
|---|---|
| Build output | `submit-build-output`, `download-build-output`, `download-source`, `skip-build-output` |
| Data exchange | `register-submit-dir`, `register-fetch-dir`, `submit`, `fetch` |
| Shared filesystem | `register-shared-dir` |
| Log persistence | `register-log-dir` |
| Builder sidecar | `apply-patch-build`, `run-pov`, `apply-patch-test` |
| Service discovery | `get-service-domain` |

## Documentation

- [Full CLI reference and design](../docs/design/libCRS.md)
- [CRS Development Guide](../docs/crs-development-guide.md) - how to use libCRS in your CRS
- [Builder README](../builder/README.md) - builder sidecar API details
