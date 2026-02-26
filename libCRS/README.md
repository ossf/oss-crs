# libCRS

A Python CLI library installed in every CRS container. It provides a uniform interface for CRS containers to interact with the OSS-CRS infrastructure - managing build outputs, exchanging data (seeds, PoVs, patches), sharing files between containers, communicating with builder sidecars, and resolving service endpoints.

## Installation

```dockerfile
COPY --from=libcrs . /opt/libCRS
RUN /opt/libCRS/install.sh
```

This installs the `libCRS` CLI at `/usr/local/bin/libCRS`.

## Commands

| Category | Commands |
|---|---|
| Build output | `submit-build-output`, `download-build-output`, `skip-build-output` |
| Data exchange | `register-submit-dir`, `register-fetch-dir`, `submit`, `fetch` |
| Shared filesystem | `register-shared-dir` |
| Builder sidecar | `apply-patch-build`, `run-pov`, `run-test` |
| Service discovery | `get-service-domain` |

## Documentation

- [Full CLI reference and design](../docs/design/libCRS.md)
- [CRS Development Guide](../docs/crs-development-guide.md) - how to use libCRS in your CRS
- [Builder README](../builder/README.md) - builder sidecar API details
