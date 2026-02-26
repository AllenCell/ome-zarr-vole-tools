"""Tests for config.py — Pydantic models, YAML loading, validation."""

from pathlib import Path

import pytest
import yaml

from ome_zarr_vole_tools.config import (
    ConversionConfig,
    FileConfig,
    PhysicalPixelSizes,
    load_yaml_config,
)


class TestPhysicalPixelSizes:
    def test_valid(self):
        ps = PhysicalPixelSizes(x=0.65, y=0.65, z=2.0)
        assert ps.x == 0.65
        assert ps.z == 2.0

    def test_z_optional(self):
        ps = PhysicalPixelSizes(x=0.65, y=0.65)
        assert ps.z is None

    def test_negative_rejected(self):
        with pytest.raises(ValueError, match="positive"):
            PhysicalPixelSizes(x=-1.0, y=0.65)


class TestFileConfig:
    def test_chunk_size_positive(self):
        with pytest.raises(ValueError, match="positive"):
            FileConfig(path="x.tif", chunk_size=(1, 1, -1, 64, 64))

    def test_scale_factor_range(self):
        with pytest.raises(ValueError, match="between 2 and 8"):
            FileConfig(path="x.tif", scale_factor=1)

    def test_resolve_paths_glob(self, tmp_path: Path):
        for i in range(3):
            (tmp_path / f"img_{i}.tif").touch()
        fc = FileConfig(path=str(tmp_path / "*.tif"))
        paths = fc.resolve_paths()
        assert len(paths) == 3

    def test_resolve_paths_no_match(self):
        fc = FileConfig(path="/nonexistent/path/*.xyz")
        with pytest.raises(FileNotFoundError, match="No files matched"):
            fc.resolve_paths()


class TestConversionConfig:
    def test_no_files_rejected(self):
        with pytest.raises(ValueError, match="At least one file"):
            ConversionConfig(files=[])

    def test_defaults(self, tmp_path: Path):
        (tmp_path / "a.tif").touch()
        cfg = ConversionConfig(files=[FileConfig(path=str(tmp_path / "a.tif"))])
        assert cfg.chunk_size == (1, 1, 64, 512, 512)
        assert cfg.pyramid_levels == "auto"
        assert cfg.compression == "blosc"

    def test_merge_per_file_override(self, tmp_path: Path):
        (tmp_path / "a.tif").touch()
        fc = FileConfig(
            path=str(tmp_path / "a.tif"),
            chunk_size=(1, 1, 32, 128, 128),
            compression="zlib",
        )
        cfg = ConversionConfig(files=[fc])
        merged = cfg.merged_config_for_file(fc)
        assert merged["chunk_size"] == (1, 1, 32, 128, 128)
        assert merged["compression"] == "zlib"
        # Global defaults for non-overridden fields
        assert merged["scale_factor"] == 2

    def test_invalid_max_workers(self, tmp_path: Path):
        (tmp_path / "a.tif").touch()
        with pytest.raises(ValueError, match="max_workers"):
            ConversionConfig(
                files=[FileConfig(path=str(tmp_path / "a.tif"))],
                max_workers=0,
            )


class TestLoadYamlConfig:
    def test_load_valid(self, sample_yaml_config: Path):
        cfg = load_yaml_config(sample_yaml_config)
        assert len(cfg.files) == 1
        assert cfg.overwrite is True
        assert cfg.chunk_size == (1, 1, 4, 64, 64)

    def test_load_with_string_files(self, tmp_path: Path, tmp_tiff: Path):
        config = tmp_path / "simple.yaml"
        out_str = str(tmp_path).replace("\\", "/")
        tiff_str = str(tmp_tiff).replace("\\", "/")
        config.write_text(
            f"""\
output: "{out_str}"
files:
  - "{tiff_str}"
"""
        )
        cfg = load_yaml_config(config)
        assert len(cfg.files) == 1
        assert cfg.files[0].path == tiff_str

    def test_load_invalid_yaml(self, tmp_path: Path):
        config = tmp_path / "bad.yaml"
        config.write_text("just a string")
        with pytest.raises(ValueError, match="mapping"):
            load_yaml_config(config)
