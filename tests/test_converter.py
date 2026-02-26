"""Tests for converter.py — unit tests and end-to-end conversion."""

from pathlib import Path

import numpy as np
import pytest
import zarr

from ome_zarr_vole_tools.converter import (
    adapt_chunk_size,
    compute_pyramid_levels,
    convert_single_file,
)


class TestComputePyramidLevels:
    def test_small_image(self):
        # Image smaller than min_size -> 1 level
        assert compute_pyramid_levels((32, 32)) == 1

    def test_exact_min(self):
        assert compute_pyramid_levels((64, 64)) == 1

    def test_512(self):
        levels = compute_pyramid_levels((512, 512), scale_factor=2)
        assert levels >= 3  # 512 -> 256 -> 128 -> 64

    def test_asymmetric(self):
        # Uses min dimension
        levels = compute_pyramid_levels((1024, 128), scale_factor=2)
        assert levels == compute_pyramid_levels((128, 128), scale_factor=2)

    def test_large_scale_factor(self):
        levels = compute_pyramid_levels((512, 512), scale_factor=4)
        assert levels < compute_pyramid_levels((512, 512), scale_factor=2)


class TestAdaptChunkSize:
    def test_clamp(self):
        result = adapt_chunk_size((1, 1, 64, 512, 512), (1, 2, 4, 30, 30))
        assert result == (1, 1, 4, 30, 30)

    def test_shorter_chunks(self):
        # 3D chunks for a 5D array -> pads with 1s
        result = adapt_chunk_size((64, 256, 256), (1, 1, 10, 100, 100))
        assert result == (1, 1, 10, 100, 100)

    def test_longer_chunks(self):
        # 5D chunks for a 3D array -> trims leading dims
        result = adapt_chunk_size((1, 1, 64, 256, 256), (10, 100, 100))
        assert result == (10, 100, 100)

    def test_no_change_needed(self):
        result = adapt_chunk_size((1, 1, 4, 64, 64), (1, 1, 4, 64, 64))
        assert result == (1, 1, 4, 64, 64)


class TestConvertSingleFile:
    def test_basic_conversion(self, tmp_tiff: Path, tmp_path: Path):
        out_dir = tmp_path / "out"
        result = convert_single_file(
            input_path=tmp_tiff,
            output_dir=str(out_dir),
            chunk_size=(1, 1, 4, 64, 64),
            pyramid_levels=1,
            overwrite=True,
        )
        assert result.exists()
        assert result.suffix == ".zarr"

        # Verify zarr structure
        store = zarr.open(str(result), mode="r")
        assert "0" in store  # At least base resolution level
        arr = store["0"]
        assert arr.ndim == 5  # TCZYX

    def test_pyramid_levels(self, tmp_tiff_large: Path, tmp_path: Path):
        out_dir = tmp_path / "out"
        result = convert_single_file(
            input_path=tmp_tiff_large,
            output_dir=str(out_dir),
            pyramid_levels="auto",
            scale_factor=2,
            overwrite=True,
        )
        store = zarr.open(str(result), mode="r")
        # Should have multiple resolution levels
        levels = [k for k in store.keys() if k.isdigit()]
        assert len(levels) >= 2

    def test_overwrite_protection(self, tmp_tiff: Path, tmp_path: Path):
        out_dir = tmp_path / "out"
        # First conversion
        convert_single_file(
            input_path=tmp_tiff,
            output_dir=str(out_dir),
            pyramid_levels=1,
            overwrite=True,
        )
        # Second without overwrite should fail
        with pytest.raises(FileExistsError, match="overwrite"):
            convert_single_file(
                input_path=tmp_tiff,
                output_dir=str(out_dir),
                pyramid_levels=1,
                overwrite=False,
            )

    def test_overwrite_allowed(self, tmp_tiff: Path, tmp_path: Path):
        out_dir = tmp_path / "out"
        convert_single_file(
            input_path=tmp_tiff,
            output_dir=str(out_dir),
            pyramid_levels=1,
            overwrite=True,
        )
        # Should succeed with overwrite=True
        result = convert_single_file(
            input_path=tmp_tiff,
            output_dir=str(out_dir),
            pyramid_levels=1,
            overwrite=True,
        )
        assert result.exists()

    def test_missing_input(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            convert_single_file(
                input_path=tmp_path / "nonexistent.tif",
                output_dir=str(tmp_path),
            )

    def test_custom_compression(self, tmp_tiff: Path, tmp_path: Path):
        for comp in ("blosc", "zlib", "none"):
            out_dir = tmp_path / f"out_{comp}"
            result = convert_single_file(
                input_path=tmp_tiff,
                output_dir=str(out_dir),
                compression=comp,
                pyramid_levels=1,
                overwrite=True,
            )
            assert result.exists()

    def test_pixel_sizes_written(self, tmp_tiff: Path, tmp_path: Path):
        out_dir = tmp_path / "out"
        result = convert_single_file(
            input_path=tmp_tiff,
            output_dir=str(out_dir),
            pixel_sizes={"x": 0.65, "y": 0.65, "z": 2.0},
            pyramid_levels=1,
            overwrite=True,
        )
        store = zarr.open(str(result), mode="r")
        meta = store.attrs.get("multiscales", [{}])[0]
        datasets = meta.get("datasets", [])
        assert len(datasets) >= 1
        transforms = datasets[0].get("coordinateTransformations", [])
        assert len(transforms) >= 1
