"""Validated, credential-free configuration shared by foreground and detached runs.

Only these foundation options are represented, not every legacy runtime flag.
Worker FORCED_* settings and deprecated top-level AGENT/MODEL aliases never
inherit SUB_COORD_* settings. Records are a whitelist, never environment dumps.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import math
from typing import Mapping
import warnings


@dataclass(frozen=True)
class RuntimeConfig:
    forced_agent: str | None = None
    forced_model: str | None = None
    sub_coord_agent: str = "codex"
    sub_coord_model: str = "gpt-6.1-sol"
    parallel_jobs: int = 4
    poll_interval_seconds: float = 5.0

    def __post_init__(self) -> None:
        if type(self.parallel_jobs) is not int or self.parallel_jobs < 1:
            raise ValueError("parallel_jobs must be a positive integer")
        if isinstance(self.poll_interval_seconds, bool) or not isinstance(self.poll_interval_seconds, (int, float)) or not math.isfinite(self.poll_interval_seconds) or self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive and finite")
        for name in ("forced_agent", "forced_model", "sub_coord_agent", "sub_coord_model"):
            value = getattr(self, name)
            if value is None and name.startswith("forced_"):
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a nonempty string")

    @classmethod
    def from_sources(cls, cli: dict, env: Mapping[str, str]) -> RuntimeConfig:
        values = {}
        for field in fields(cls):
            name = field.name
            value = cli.get(name)
            alias = name.removeprefix("forced_") if name.startswith("forced_") else None
            used_alias = False
            if value is None and alias is not None:
                value = cli.get(alias)
                used_alias = value is not None
            if value is None:
                value = env.get(name.upper())
            if value is None and alias is not None:
                value = env.get(alias.upper())
                used_alias = value is not None
            if used_alias:
                warnings.warn(f"{alias.upper()} is deprecated; use {name.upper()}", DeprecationWarning, stacklevel=2)
            if value is not None:
                values[name] = value
        if isinstance(values.get("parallel_jobs"), str):
            values["parallel_jobs"] = int(values["parallel_jobs"])
        if isinstance(values.get("poll_interval_seconds"), str):
            values["poll_interval_seconds"] = float(values["poll_interval_seconds"])
        return cls(**values)

    def to_record(self) -> dict:
        return {"schema_version": 1, **{field.name: getattr(self, field.name) for field in fields(self)}}

    @classmethod
    def from_record(cls, record: Mapping) -> RuntimeConfig:
        if type(record.get("schema_version")) is not int or record["schema_version"] != 1:
            raise ValueError("unsupported configuration schema_version")
        return cls(**{field.name: record[field.name] for field in fields(cls) if field.name in record})
