"""Tests for url.py — VolE viewer URL generation."""

import pytest

from ome_zarr_vole_tools.url import build_viewer_url


class TestBuildViewerUrl:
    def test_unix_path(self):
        url = build_viewer_url("/allen/aics/emt/xyz.zarr")
        assert url == (
            "https://vol-e.allencell.org/viewer"
            "?url=https%3A%2F%2Fvast-files.int.allencell.org%2Femt%2Fxyz.zarr"
        )

    def test_windows_unc_path(self):
        url = build_viewer_url("\\\\allen\\aics\\emt\\xyz.zarr")
        assert url == (
            "https://vol-e.allencell.org/viewer"
            "?url=https%3A%2F%2Fvast-files.int.allencell.org%2Femt%2Fxyz.zarr"
        )

    def test_forward_slash_unc(self):
        url = build_viewer_url("//allen/aics/emt/xyz.zarr")
        assert url == (
            "https://vol-e.allencell.org/viewer"
            "?url=https%3A%2F%2Fvast-files.int.allencell.org%2Femt%2Fxyz.zarr"
        )

    def test_tiff_extension_kept(self):
        url = build_viewer_url("/allen/aics/projects/img.ome.tiff")
        assert "img.ome.tiff" in url

    def test_deep_nested_path(self):
        url = build_viewer_url("/allen/aics/assay-dev/MicroscopyData/2024/exp1/run.zarr")
        assert "assay-dev%2FMicroscopyData%2F2024%2Fexp1%2Frun.zarr" in url

    def test_trailing_slash_stripped(self):
        url = build_viewer_url("/allen/aics/emt/xyz.zarr/")
        assert url.endswith("emt%2Fxyz.zarr")

    def test_case_insensitive_prefix(self):
        url = build_viewer_url("/Allen/AICS/emt/xyz.zarr")
        assert "emt%2Fxyz.zarr" in url

    def test_invalid_path_raises(self):
        with pytest.raises(ValueError, match="must start with"):
            build_viewer_url("/some/other/path.zarr")

    def test_missing_prefix_raises(self):
        with pytest.raises(ValueError, match="must start with"):
            build_viewer_url("emt/xyz.zarr")
