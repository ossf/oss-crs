# SPDX-License-Identifier: MIT
"""API contract schemas for builder-sidecar and runner-sidecar HTTP endpoints.

Used by test suites and sidecar clients to validate request/response payloads
against expected schemas, preventing mock drift.
"""

from pydantic import BaseModel, ConfigDict


class BuildResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    rebuild_id: int | None = None


class JobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: str  # "queued" | "running" | "done" | "error"
    result: BuildResult | None = None
    error: str | None = None


class POVResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exit_code: int
    stdout: str = ""
    stderr: str = ""
