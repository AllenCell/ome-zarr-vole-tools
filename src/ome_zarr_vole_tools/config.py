"""Pydantic models for conversion configuration and YAML loading."""

from __future__ import annotations

import glob as globmod
from pathlib import Path
from typing import List, Literal, Optional, Tuple

import yaml
from pydantic import BaseModel, field_validator, model_validator


class PhysicalPixelSizes(BaseModel):
    """Physical pixel sizes in microns."""

    x: float
    y: float
    z: Optional[float] = None

    @field_validator("x", "y", "z")
    @classmethod
    def positive(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            raise ValueError("Pixel size must be positive")
        return v


class FileConfig(BaseModel):
    """Configuration for a single input file (or glob pattern)."""

    path: str
    output: Optional[str] = None
    chunk_size: Optional[Tuple[int, ...]] = None
    pyramid_levels: Optional[int | Literal["auto"]] = None
    scale_factor: Optional[int] = None
    compression: Optional[Literal["blosc", "zlib", "none"]] = None
    pixel_sizes: Optional[PhysicalPixelSizes] = None
    channel_names: Optional[List[str]] = None

    @field_validator("chunk_size")
    @classmethod
    def chunk_size_positive(cls, v: Optional[Tuple[int, ...]]) -> Optional[Tuple[int, ...]]:
        if v is not None:
            if not all(c > 0 for c in v):
                raise ValueError("All chunk dimensions must be positive")
        return v

    @field_validator("scale_factor")
    @classmethod
    def scale_factor_range(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not (2 <= v <= 8):
            raise ValueError("Scale factor must be between 2 and 8")
        return v

    def resolve_paths(self) -> List[Path]:
        """Expand glob patterns into concrete file paths."""
        matches = sorted(globmod.glob(self.path, recursive=True))
        if not matches:
            raise FileNotFoundError(f"No files matched pattern: {self.path}")
        return [Path(m) for m in matches]


class ConversionConfig(BaseModel):
    """Top-level configuration: global defaults + per-file overrides."""

    output: str = "."
    chunk_size: Tuple[int, ...] = (1, 1, 64, 512, 512)
    pyramid_levels: int | Literal["auto"] = "auto"
    scale_factor: int = 2
    compression: Literal["blosc", "zlib", "none"] = "blosc"
    pixel_sizes: Optional[PhysicalPixelSizes] = None
    channel_names: Optional[List[str]] = None
    max_workers: int = 1
    overwrite: bool = False
    files: List[FileConfig] = []

    @field_validator("chunk_size")
    @classmethod
    def chunk_size_positive(cls, v: Tuple[int, ...]) -> Tuple[int, ...]:
        if not all(c > 0 for c in v):
            raise ValueError("All chunk dimensions must be positive")
        return v

    @field_validator("scale_factor")
    @classmethod
    def scale_factor_range(cls, v: int) -> int:
        if not (2 <= v <= 8):
            raise ValueError("Scale factor must be between 2 and 8")
        return v

    @field_validator("max_workers")
    @classmethod
    def max_workers_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_workers must be >= 1")
        return v

    @model_validator(mode="after")
    def validate_files_present(self) -> "ConversionConfig":
        if not self.files:
            raise ValueError("At least one file entry is required")
        return self

    def merged_config_for_file(self, fc: FileConfig) -> dict:
        """Return a flat dict of settings for one file, with per-file overrides winning."""
        return {
            "output": fc.output or self.output,
            "chunk_size": fc.chunk_size or self.chunk_size,
            "pyramid_levels": fc.pyramid_levels if fc.pyramid_levels is not None else self.pyramid_levels,
            "scale_factor": fc.scale_factor or self.scale_factor,
            "compression": fc.compression or self.compression,
            "pixel_sizes": fc.pixel_sizes or self.pixel_sizes,
            "channel_names": fc.channel_names or self.channel_names,
            "overwrite": self.overwrite,
        }


def _parse_chunk_size(raw) -> Optional[Tuple[int, ...]]:
    """Parse chunk_size from various YAML representations."""
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        return tuple(int(x) for x in raw)
    if isinstance(raw, str):
        return tuple(int(x) for x in raw.split(","))
    raise ValueError(f"Cannot parse chunk_size: {raw}")


def load_yaml_config(path: str | Path) -> ConversionConfig:
    """Load and validate a YAML configuration file."""
    path = Path(path)
    with open(path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError("YAML config must be a mapping at the top level")

    # Parse top-level chunk_size
    if "chunk_size" in raw:
        raw["chunk_size"] = _parse_chunk_size(raw["chunk_size"])

    # Parse per-file configs
    file_list = raw.pop("files", [])
    parsed_files = []
    for entry in file_list:
        if isinstance(entry, str):
            entry = {"path": entry}
        if "chunk_size" in entry:
            entry["chunk_size"] = _parse_chunk_size(entry["chunk_size"])
        if "pixel_sizes" in entry and isinstance(entry["pixel_sizes"], dict):
            entry["pixel_sizes"] = PhysicalPixelSizes(**entry["pixel_sizes"])
        parsed_files.append(FileConfig(**entry))

    # Parse top-level pixel_sizes
    if "pixel_sizes" in raw and isinstance(raw["pixel_sizes"], dict):
        raw["pixel_sizes"] = PhysicalPixelSizes(**raw["pixel_sizes"])

    return ConversionConfig(**raw, files=parsed_files)
