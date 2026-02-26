"""Shared test fixtures — synthetic TIFF generation."""

from pathlib import Path

import numpy as np
import pytest
import tifffile


@pytest.fixture
def tmp_tiff(tmp_path: Path) -> Path:
    """Create a minimal 3D TIFF (single T, single C, small ZYX)."""
    data = np.random.randint(0, 255, size=(4, 64, 64), dtype=np.uint8)
    path = tmp_path / "test_image.tif"
    tifffile.imwrite(str(path), data)
    return path


@pytest.fixture
def tmp_tiff_multichannel(tmp_path: Path) -> Path:
    """Create a multi-channel TIFF: shape (2, 4, 64, 64) interpreted as CZYX."""
    data = np.random.randint(0, 255, size=(2, 4, 64, 64), dtype=np.uint8)
    path = tmp_path / "multichannel.tif"
    tifffile.imwrite(str(path), data)
    return path


@pytest.fixture
def tmp_tiff_large(tmp_path: Path) -> Path:
    """Create a larger TIFF to test pyramid generation: 512x512."""
    data = np.random.randint(0, 255, size=(512, 512), dtype=np.uint8)
    path = tmp_path / "large_image.tif"
    tifffile.imwrite(str(path), data)
    return path


@pytest.fixture
def tmp_timelapse_dir(tmp_path: Path) -> Path:
    """Create a directory with 5 TIFF files representing timepoints (ZYX each)."""
    tl_dir = tmp_path / "timelapse"
    tl_dir.mkdir()
    for i in range(5):
        data = np.random.randint(0, 255, size=(4, 64, 64), dtype=np.uint8)
        tifffile.imwrite(str(tl_dir / f"tp_{i:03d}.tif"), data)
    return tl_dir


@pytest.fixture
def tmp_timelapse_dir_mismatched(tmp_path: Path) -> Path:
    """Create a directory with TIFFs of different shapes (should cause error)."""
    tl_dir = tmp_path / "timelapse_bad"
    tl_dir.mkdir()
    tifffile.imwrite(str(tl_dir / "tp_000.tif"),
                     np.zeros((4, 64, 64), dtype=np.uint8))
    tifffile.imwrite(str(tl_dir / "tp_001.tif"),
                     np.zeros((4, 32, 32), dtype=np.uint8))
    return tl_dir


@pytest.fixture
def sample_yaml_config(tmp_path: Path, tmp_tiff: Path) -> Path:
    """Write a minimal valid YAML config referencing the tmp_tiff."""
    config = tmp_path / "config.yaml"
    # Use forward slashes to avoid YAML escape issues on Windows
    out_str = str(tmp_path / "zarr_out").replace("\\", "/")
    tiff_str = str(tmp_tiff).replace("\\", "/")
    config.write_text(
        f"""\
output: "{out_str}"
chunk_size: 1,1,4,64,64
pyramid_levels: auto
scale_factor: 2
compression: blosc
overwrite: true
files:
  - path: "{tiff_str}"
"""
    )
    return config
