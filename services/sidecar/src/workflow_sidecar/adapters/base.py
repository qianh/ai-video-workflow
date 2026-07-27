"""Shared media result types."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MediaResult:
    ok: bool
    adapter: str
    output_path: Path | None
    mime_type: str
    duration_ms: int = 0
    width: int | None = None
    height: int | None = None
    degraded: bool = False
    mock: bool = False
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.output_path is not None:
            data["output_path"] = str(self.output_path)
        return data
