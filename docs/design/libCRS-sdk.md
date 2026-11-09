# libCRS Python SDK Reference & Developer Guide

## Overview

In addition to its command-line interface, `libCRS` provides a native Python library (`libcrs`) that can be directly imported into Python-based CRS agents, fuzzing orchestrators, and LLM reasoning pipelines.

---

## 1. Installation

When installing libCRS via `install.sh` with the default flags or `--python`:

```dockerfile
COPY libCRS /opt/libCRS
RUN /opt/libCRS/install.sh --python
```

Verify in Python:
```python
import libcrs

print(libcrs.__file__)
```

---

## 2. Core Architecture: The `CRSUtils` Strategy Pattern

`libCRS` uses the **strategy pattern** to decouple your agent's code from the underlying deployment environment:

- **`CRSUtils` (Abstract Base Class)**: Defines the standard interface for submissions, fetches, sidecar calls, and path resolution.
- **`LocalCRSUtils`**: Implements `CRSUtils` for local multi-container Docker Compose environments.
- **`AzureCRSUtils` (Planned)**: Will implement `CRSUtils` for cloud deployment backends.

The factory function `get_crs_utils()` automatically instantiates the appropriate backend based on the container's environment variables:

```python
from libCRS.local import LocalCRSUtils, get_crs_utils
from libCRS.base import DataType

# Automatically detects environment and initializes backend
utils = get_crs_utils()
```

---

## 3. Python API Examples

### Submitting a Discovered PoV (Crash Input)
```python
from pathlib import Path
from libCRS.local import get_crs_utils
from libCRS.base import DataType

utils = get_crs_utils()

# Submit a crash input found by your fuzzer
pov_file = Path("/work/fuzzer/crashes/crash_001.bin")
if pov_file.exists():
    submitted_path = utils.submit_file(DataType.POV, pov_file)
    print(f"Successfully submitted PoV to infrastructure: {submitted_path}")
```

### Submitting Structured Findings (SARIF Reports)
```python
import json
from pathlib import Path
from libCRS.local import get_crs_utils
from libCRS.base import DataType
from libCRS.sarif import validate_sarif

utils = get_crs_utils()

sarif_doc = {
    "version": "2.1.0",
    "runs": [
        {
            "tool": {"driver": {"name": "MyLLMScanner", "version": "1.0.0"}},
            "results": [
                {
                    "ruleId": "CWE-120",
                    "message": {"text": "Buffer copy without checking size of input"},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": "src/parser.c"},
                                "region": {"startLine": 45},
                            }
                        }
                    ],
                }
            ],
        }
    ],
}

# Validate SARIF compliance before submission
errors = validate_sarif(sarif_doc)
if not errors:
    report_file = Path("/tmp/finding.sarif")
    report_file.write_text(json.dumps(sarif_doc))
    utils.submit_file(DataType.BUG_CANDIDATE, report_file)
```

### Requesting a Rebuild via Builder Sidecar
```python
from pathlib import Path
from libCRS.sidecar_client import BuilderSidecarClient

sidecar = BuilderSidecarClient()
patch_file = Path("/work/patches/fix_null_deref.patch")
out_dir = Path("/work/rebuild_output")

# Send patch to builder sidecar
result = sidecar.apply_patch_build(patch_file, out_dir)
if result.get("status") == "success":
    rebuild_id = result["rebuild_id"]
    print(f"Build succeeded! Rebuild ID: {rebuild_id}")

    # Replay PoV against patched binary
    pov_file = Path("/work/povs/poc.bin")
    test_result = sidecar.run_pov(
        pov_file, out_dir, harness="xml", rebuild_id=rebuild_id
    )
    if not test_result.get("crashed"):
        print("Verification passed: PoV no longer crashes patched binary!")
```
