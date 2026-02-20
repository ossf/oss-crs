from typing import Optional

from pydantic import BaseModel, Field


class CRSArtifacts(BaseModel):
    """Artifacts for a single CRS."""

    build: Optional[str] = None
    pov: Optional[str] = None
    seed: Optional[str] = None
    fetch: Optional[str] = None
    shared: Optional[str] = None


class ArtifactsOutput(BaseModel):
    """Complete artifacts output structure."""

    build_id: str
    run_id: str
    crs: dict[str, CRSArtifacts] = Field(default_factory=dict)

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return self.model_dump_json(indent=indent, exclude_none=True)
