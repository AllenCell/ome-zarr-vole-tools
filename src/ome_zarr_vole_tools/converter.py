"""Core conversion engine: microscopy image -> OME-Zarr."""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple

import dask.array as da
import numpy as np
import zarr
from bioio import BioImage
from ome_zarr.io import parse_url
from ome_zarr.writer import write_multiscale

logger = logging.getLogger(__name__)


def compute_pyramid_levels(
    shape_yx: Tuple[int, int],
    scale_factor: int = 2,
    min_size: int = 64,
) -> int:
    """Compute how many pyramid levels are needed so the smallest level >= min_size."""
    min_dim = min(shape_yx)
    if min_dim <= min_size:
        return 1
    levels = 1 + int(math.log(min_dim / min_size, scale_factor))
    return max(1, levels)


def adapt_chunk_size(
    requested: Tuple[int, ...],
    shape: Tuple[int, ...],
) -> Tuple[int, ...]:
    """Clamp each chunk dimension to the actual array dimension."""
    if len(requested) < len(shape):
        # Pad with 1s on the left for missing leading dims
        pad = len(shape) - len(requested)
        requested = (1,) * pad + requested
    elif len(requested) > len(shape):
        # Trim from the left
        requested = requested[-len(shape):]
    return tuple(min(c, s) for c, s in zip(requested, shape))


def build_axes_metadata(ndim: int) -> List[Dict[str, str]]:
    """Build OME-Zarr axes metadata for a 5D TCZYX array (or fewer dims)."""
    full_axes = [
        {"name": "t", "type": "time"},
        {"name": "c", "type": "channel"},
        {"name": "z", "type": "space", "unit": "micrometer"},
        {"name": "y", "type": "space", "unit": "micrometer"},
        {"name": "x", "type": "space", "unit": "micrometer"},
    ]
    return full_axes[-ndim:]


def build_coordinate_transformations(
    num_levels: int,
    scale_factor: int,
    pixel_sizes: Optional[Dict[str, float]] = None,
    ndim: int = 5,
) -> List[List[Dict[str, Any]]]:
    """Build per-level coordinate transformations (scale only)."""
    # Base scale: 1.0 for T, C; physical sizes for Z, Y, X
    base = [1.0] * ndim
    if pixel_sizes:
        if ndim >= 1 and pixel_sizes.get("x"):
            base[-1] = pixel_sizes["x"]
        if ndim >= 2 and pixel_sizes.get("y"):
            base[-2] = pixel_sizes["y"]
        if ndim >= 3 and pixel_sizes.get("z"):
            base[-3] = pixel_sizes["z"]

    transforms = []
    for level in range(num_levels):
        scale = list(base)
        # Only scale spatial dims (last 2: Y, X) by level
        spatial_mult = scale_factor ** level
        scale[-1] *= spatial_mult
        scale[-2] *= spatial_mult
        transforms.append([{"type": "scale", "scale": scale}])
    return transforms


def get_compressor(compression: str):
    """Return a Zarr compressor instance."""
    if compression == "blosc":
        from numcodecs import Blosc
        return Blosc(cname="lz4", clevel=5, shuffle=Blosc.BITSHUFFLE)
    elif compression == "zlib":
        from numcodecs import Zlib
        return Zlib(level=5)
    elif compression == "none":
        return None
    raise ValueError(f"Unknown compression: {compression}")


def _build_pyramid(
    base_data: da.Array,
    num_levels: int,
    scale_factor: int,
) -> List[da.Array]:
    """Build a multi-resolution pyramid by coarsening spatial dims."""
    pyramid = [base_data]
    current = base_data
    for _ in range(1, num_levels):
        # Coarsen the last two spatial dims (Y, X)
        ndim = current.ndim
        coarsen_axes = {}
        # Y axis
        if current.shape[-2] >= scale_factor:
            coarsen_axes[ndim - 2] = scale_factor
        # X axis
        if current.shape[-1] >= scale_factor:
            coarsen_axes[ndim - 1] = scale_factor

        if not coarsen_axes:
            break

        # Trim to make dimensions divisible by scale_factor
        slices = [slice(None)] * ndim
        for ax, sf in coarsen_axes.items():
            trim = current.shape[ax] - (current.shape[ax] % sf)
            slices[ax] = slice(0, trim)
        trimmed = current[tuple(slices)]

        current = da.coarsen(np.mean, trimmed, coarsen_axes, trim_excess=True)
        pyramid.append(current)

    return pyramid


def convert_single_file(
    input_path: Path,
    output_dir: str,
    chunk_size: Tuple[int, ...] = (1, 1, 64, 512, 512),
    pyramid_levels: int | Literal["auto"] = "auto",
    scale_factor: int = 2,
    compression: str = "blosc",
    pixel_sizes: Optional[Dict[str, float]] = None,
    channel_names: Optional[List[str]] = None,
    overwrite: bool = False,
) -> Path:
    """Convert a single microscopy image to OME-Zarr.

    Returns the path to the created .zarr directory.
    """
    input_path = Path(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Determine output path
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    zarr_path = out_dir / f"{input_path.stem}.zarr"

    if zarr_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {zarr_path}  (use --overwrite to replace)"
        )

    logger.info("Reading %s", input_path)
    img = BioImage(input_path)

    # Get 5D dask array (TCZYX)
    data = img.get_image_dask_data("TCZYX")
    logger.info("Image shape (TCZYX): %s, dtype: %s", data.shape, data.dtype)

    # Merge pixel sizes: user overrides > file metadata
    merged_pixel_sizes = {}
    file_ps = img.physical_pixel_sizes
    if file_ps.X:
        merged_pixel_sizes["x"] = file_ps.X
    if file_ps.Y:
        merged_pixel_sizes["y"] = file_ps.Y
    if file_ps.Z:
        merged_pixel_sizes["z"] = file_ps.Z
    if pixel_sizes:
        merged_pixel_sizes.update({k: v for k, v in pixel_sizes.items() if v is not None})

    # Merge channel names: user overrides > file metadata
    merged_channels = channel_names
    if not merged_channels:
        try:
            merged_channels = list(img.channel_names) if img.channel_names else None
        except Exception:
            merged_channels = None

    # Adapt chunks
    chunks = adapt_chunk_size(chunk_size, data.shape)
    data = data.rechunk(chunks)
    logger.info("Chunks: %s", chunks)

    # Compute pyramid levels
    shape_yx = (data.shape[-2], data.shape[-1])
    if pyramid_levels == "auto":
        num_levels = compute_pyramid_levels(shape_yx, scale_factor)
    else:
        num_levels = int(pyramid_levels)
    logger.info("Pyramid levels: %d (scale factor: %d)", num_levels, scale_factor)

    # Build pyramid
    pyramid = _build_pyramid(data, num_levels, scale_factor)

    # Compressor
    compressor = get_compressor(compression)

    # Write OME-Zarr
    store = parse_url(str(zarr_path), mode="w").store
    root = zarr.open_group(store, mode="w")

    axes = build_axes_metadata(data.ndim)
    coordinate_transformations = build_coordinate_transformations(
        num_levels, scale_factor, merged_pixel_sizes, data.ndim
    )

    storage_options = {"compressor": compressor, "chunks": chunks}

    write_multiscale(
        pyramid=pyramid,
        group=root,
        axes=[a["name"] for a in axes],
        coordinate_transformations=coordinate_transformations,
        storage_options=storage_options,
        name=input_path.stem,
    )

    # write_multiscale handles the "multiscales" attr; add channel metadata
    if merged_channels:
        omero = {
            "channels": [
                {"label": name, "color": "FFFFFF", "active": True, "window": {}}
                for name in merged_channels
            ]
        }
        root.attrs["omero"] = omero

    logger.info("Written OME-Zarr to %s", zarr_path)
    return zarr_path


def _natural_sort_key(path: Path):
    """Sort key that handles embedded numbers naturally (img1, img2, img10)."""
    parts = re.split(r"(\d+)", path.name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def convert_timelapse(
    input_dir: Path,
    output_dir: str,
    output_name: Optional[str] = None,
    pattern: str = "*.tif*",
    chunk_size: Tuple[int, ...] = (1, 1, 64, 512, 512),
    pyramid_levels: int | Literal["auto"] = "auto",
    scale_factor: int = 2,
    compression: str = "blosc",
    pixel_sizes: Optional[Dict[str, float]] = None,
    channel_names: Optional[List[str]] = None,
    overwrite: bool = False,
) -> Path:
    """Combine a directory of TIFF files (one per timepoint) into a single timelapse OME-Zarr.

    Each TIFF is read as a CZYX volume (or lower-dimensional and expanded to CZYX).
    Files are sorted naturally by filename to determine timepoint order, then
    stacked along a new T dimension to form a 5D TCZYX array.

    Returns the path to the created .zarr directory.
    """
    input_dir = Path(input_dir)
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Not a directory: {input_dir}")

    # Collect and sort TIFF files
    import glob as globmod

    tiff_files = sorted(
        [Path(p) for p in globmod.glob(str(input_dir / pattern))],
        key=_natural_sort_key,
    )

    if not tiff_files:
        raise FileNotFoundError(f"No files matching '{pattern}' in {input_dir}")

    logger.info("Found %d timepoint files in %s", len(tiff_files), input_dir)

    # Read each timepoint as CZYX and collect as dask arrays
    timepoint_arrays = []
    first_shape = None
    first_dtype = None

    for i, tiff_path in enumerate(tiff_files):
        logger.info("Reading timepoint %d: %s", i, tiff_path.name)
        img = BioImage(tiff_path)
        # Get as CZYX (single timepoint)
        data = img.get_image_dask_data("CZYX")
        if first_shape is None:
            first_shape = data.shape
            first_dtype = data.dtype
        else:
            if data.shape != first_shape:
                raise ValueError(
                    f"Shape mismatch at timepoint {i} ({tiff_path.name}): "
                    f"expected {first_shape}, got {data.shape}"
                )

        timepoint_arrays.append(data)

    # Stack along new T axis: each is CZYX -> stack gives TCZYX
    timelapse = da.stack(timepoint_arrays, axis=0)
    logger.info("Timelapse shape (TCZYX): %s, dtype: %s", timelapse.shape, timelapse.dtype)

    # Merge pixel sizes from first file
    merged_pixel_sizes = {}
    first_img = BioImage(tiff_files[0])
    file_ps = first_img.physical_pixel_sizes
    if file_ps.X:
        merged_pixel_sizes["x"] = file_ps.X
    if file_ps.Y:
        merged_pixel_sizes["y"] = file_ps.Y
    if file_ps.Z:
        merged_pixel_sizes["z"] = file_ps.Z
    if pixel_sizes:
        merged_pixel_sizes.update({k: v for k, v in pixel_sizes.items() if v is not None})

    # Merge channel names from first file
    merged_channels = channel_names
    if not merged_channels:
        try:
            merged_channels = list(first_img.channel_names) if first_img.channel_names else None
        except Exception:
            merged_channels = None

    # Adapt chunks
    chunks = adapt_chunk_size(chunk_size, timelapse.shape)
    timelapse = timelapse.rechunk(chunks)
    logger.info("Chunks: %s", chunks)

    # Compute pyramid levels
    shape_yx = (timelapse.shape[-2], timelapse.shape[-1])
    if pyramid_levels == "auto":
        num_levels = compute_pyramid_levels(shape_yx, scale_factor)
    else:
        num_levels = int(pyramid_levels)
    logger.info("Pyramid levels: %d (scale factor: %d)", num_levels, scale_factor)

    # Build pyramid
    pyramid = _build_pyramid(timelapse, num_levels, scale_factor)

    # Compressor
    compressor = get_compressor(compression)

    # Determine output path
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = output_name or input_dir.name
    if not name.endswith(".zarr"):
        name += ".zarr"
    zarr_path = out_dir / name

    if zarr_path.exists() and not overwrite:
        raise FileExistsError(
            f"Output already exists: {zarr_path}  (use --overwrite to replace)"
        )

    # Write OME-Zarr
    store = parse_url(str(zarr_path), mode="w").store
    root = zarr.open_group(store, mode="w")

    axes = build_axes_metadata(timelapse.ndim)
    coordinate_transformations = build_coordinate_transformations(
        num_levels, scale_factor, merged_pixel_sizes, timelapse.ndim
    )

    storage_options = {"compressor": compressor, "chunks": chunks}

    write_multiscale(
        pyramid=pyramid,
        group=root,
        axes=[a["name"] for a in axes],
        coordinate_transformations=coordinate_transformations,
        storage_options=storage_options,
        name=name.replace(".zarr", ""),
    )

    # Channel metadata
    if merged_channels:
        omero = {
            "channels": [
                {"label": cname, "color": "FFFFFF", "active": True, "window": {}}
                for cname in merged_channels
            ]
        }
        root.attrs["omero"] = omero

    logger.info(
        "Written timelapse OME-Zarr (%d timepoints) to %s",
        len(tiff_files),
        zarr_path,
    )
    return zarr_path
