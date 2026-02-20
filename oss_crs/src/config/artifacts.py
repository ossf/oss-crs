from typing import Optional

from pydantic import BaseModel, Field


class ExchangeDir(BaseModel):
    """Exchange directory paths shared across all CRSs."""

    base: Optional[str] = None
    pov: Optional[str] = None
    seed: Optional[str] = None
    bug_candidate: Optional[str] = None
    patch: Optional[str] = None
    diff: Optional[str] = None


class CRSArtifacts(BaseModel):
    """Artifacts for a single CRS."""

    build: Optional[str] = None
    submit_dir: Optional[str] = None
    pov: Optional[str] = None
    seed: Optional[str] = None
    bug_candidate: Optional[str] = None
    patch: Optional[str] = None
    fetch: Optional[str] = None
    shared: Optional[str] = None


class ArtifactsOutput(BaseModel):
    """Complete artifacts output structure."""

    build_id: Optional[str] = None
    run_id: str
    sanitizer: Optional[str] = None
    exchange_dir: Optional[ExchangeDir] = None
    crs: dict[str, CRSArtifacts] = Field(default_factory=dict)

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return self.model_dump_json(indent=indent, exclude_none=True)
