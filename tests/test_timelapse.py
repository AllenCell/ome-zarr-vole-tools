"""Tests for timelapse conversion: directory of TIFFs -> single OME-Zarr."""

from pathlib import Path

import numpy as np
import pytest
import zarr

from ome_zarr_vole_tools.converter import convert_timelapse


class TestConvertTimelapse:
    def test_basic_timelapse(self, tmp_timelapse_dir: Path, tmp_path: Path):
        out_dir = tmp_path / "out"
        result = convert_timelapse(
            input_dir=tmp_timelapse_dir,
            output_dir=str(out_dir),
            pyramid_levels=1,
            overwrite=True,
        )
        assert result.exists()
        assert result.suffix == ".zarr"

        store = zarr.open(str(result), mode="r")
        assert "0" in store
        arr = store["0"]
        assert arr.ndim == 5  # TCZYX
        assert arr.shape[0] == 5  # 5 timepoints

    def test_output_name(self, tmp_timelapse_dir: Path, tmp_path: Path):
        out_dir = tmp_path / "out"
        result = convert_timelapse(
            input_dir=tmp_timelapse_dir,
            output_dir=str(out_dir),
            output_name="my_experiment",
            pyramid_levels=1,
            overwrite=True,
        )
        assert result.name == "my_experiment.zarr"

    def test_output_name_with_zarr_ext(self, tmp_timelapse_dir: Path, tmp_path: Path):
        out_dir = tmp_path / "out"
        result = convert_timelapse(
            input_dir=tmp_timelapse_dir,
            output_dir=str(out_dir),
            output_name="my_experiment.zarr",
            pyramid_levels=1,
            overwrite=True,
        )
        assert result.name == "my_experiment.zarr"

    def test_shape_mismatch_raises(self, tmp_timelapse_dir_mismatched: Path, tmp_path: Path):
        out_dir = tmp_path / "out"
        with pytest.raises(ValueError, match="Shape mismatch"):
            convert_timelapse(
                input_dir=tmp_timelapse_dir_mismatched,
                output_dir=str(out_dir),
                pyramid_levels=1,
                overwrite=True,
            )

    def test_empty_dir_raises(self, tmp_path: Path):
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(FileNotFoundError, match="No files matching"):
            convert_timelapse(
                input_dir=empty,
                output_dir=str(tmp_path / "out"),
            )

    def test_not_a_directory_raises(self, tmp_path: Path):
        fake = tmp_path / "fake.txt"
        fake.write_text("not a dir")
        with pytest.raises(NotADirectoryError):
            convert_timelapse(
                input_dir=fake,
                output_dir=str(tmp_path / "out"),
            )

    def test_overwrite_protection(self, tmp_timelapse_dir: Path, tmp_path: Path):
        out_dir = tmp_path / "out"
        convert_timelapse(
            input_dir=tmp_timelapse_dir,
            output_dir=str(out_dir),
            pyramid_levels=1,
            overwrite=True,
        )
        with pytest.raises(FileExistsError, match="overwrite"):
            convert_timelapse(
                input_dir=tmp_timelapse_dir,
                output_dir=str(out_dir),
                pyramid_levels=1,
                overwrite=False,
            )

    def test_natural_sort_order(self, tmp_path: Path):
        """Files with numbers sort naturally: tp_1, tp_2, tp_10 (not tp_1, tp_10, tp_2)."""
        tl_dir = tmp_path / "natural_sort"
        tl_dir.mkdir()
        for i in [1, 2, 10, 3]:
            data = np.random.randint(0, 255, size=(4, 64, 64), dtype=np.uint8)
            # Use value as marker in first pixel
            data[0, 0, 0] = i
            from tifffile import imwrite
            imwrite(str(tl_dir / f"tp_{i}.tif"), data)

        out_dir = tmp_path / "out"
        result = convert_timelapse(
            input_dir=tl_dir,
            output_dir=str(out_dir),
            pyramid_levels=1,
            overwrite=True,
        )
        store = zarr.open(str(result), mode="r")
        arr = store["0"]
        # Timepoints should be in order: 1, 2, 3, 10
        assert arr.shape[0] == 4

    def test_pixel_sizes_written(self, tmp_timelapse_dir: Path, tmp_path: Path):
        out_dir = tmp_path / "out"
        result = convert_timelapse(
            input_dir=tmp_timelapse_dir,
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
