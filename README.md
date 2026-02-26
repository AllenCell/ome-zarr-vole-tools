# OME-Zarr VolE Tools

A CLI tool to convert microscopy images (2D/3D/4D) to [OME-Zarr](https://ngff.openmicroscopy.org/) format and generate [VolE viewer](https://vol-e.allencell.org) URLs, using the [bioio](https://github.com/bioio-devs/bioio) ecosystem. Supports batch processing, multiple vendor formats, and multi-resolution pyramids.

---

## Table of Contents

- [Features](#features)
- [Supported Input Formats](#supported-input-formats)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [CLI Reference](#cli-reference)
  - [convert](#convert)
  - [viewer-url](#viewer-url)
- [YAML Batch Configuration](#yaml-batch-configuration)
  - [Schema Reference](#schema-reference)
  - [Config Merge Logic](#config-merge-logic)
- [Python API](#python-api)
- [Project Structure](#project-structure)
- [Development](#development)
- [Testing](#testing)

---

## Features

- **Multi-format input** — reads TIFF, OME-TIFF, CZI (Zeiss), LIF (Leica), ND2 (Nikon), and DV (DeltaVision) via bioio plugins
- **OME-Zarr v0.4 output** — spec-compliant multiscale arrays with axes metadata and coordinate transformations
- **Multi-resolution pyramids** — automatic or fixed-level downsampling with configurable scale factor
- **Flexible chunking** — user-defined chunk sizes in TCZYX order, automatically clamped to array dimensions
- **Compression options** — Blosc (default), Zlib, or no compression
- **Metadata handling** — physical pixel sizes and channel names carried from source metadata with optional user overrides
- **Batch processing** — YAML config files with global defaults and per-file overrides, glob pattern support
- **Parallel execution** — configurable worker count for multi-file batch jobs
- **VolE viewer URLs** — generate shareable viewer links from Allen Institute network paths
- **Rich output** — progress bars during conversion and a summary table of successes/failures

---

## Supported Input Formats

| Plugin | Extensions | Vendor |
|--------|-----------|--------|
| bioio-tifffile | `.tif`, `.tiff` | Generic TIFF |
| bioio-ome-tiff | `.ome.tif`, `.ome.tiff` | OME-TIFF |
| bioio-czi | `.czi` | Zeiss |
| bioio-lif | `.lif` | Leica |
| bioio-nd2 | `.nd2` | Nikon |
| bioio-dv | `.dv` | DeltaVision |

Format detection is automatic — bioio selects the correct reader plugin based on the file extension.

---

## Installation

**Requirements:** Python >= 3.10, conda (or mamba)

### Create a dedicated environment

```bash
conda create -n ome-zarr-vole-tools python=3.11 -y
conda activate ome-zarr-vole-tools
```

### Install the package

```bash
# From the project root
pip install -e ".[dev]"
```

This installs the `ome-zarr-vole-tools` entry point and all dependencies (bioio + 6 reader plugins, ome-zarr, zarr, dask, click, pyyaml, rich, pydantic). The `[dev]` extra adds pytest, tifffile, and numpy for running tests.

### Verify

```bash
ome-zarr-vole-tools --version
ome-zarr-vole-tools --help
```

---

## Quick Start

```bash
# Convert a single TIFF
ome-zarr-vole-tools convert image.tif -o output/

# Convert all CZI files with custom chunk size
ome-zarr-vole-tools convert "data/*.czi" -o output/ --chunk-size 1,1,32,256,256 --overwrite

# Batch conversion from a YAML config
ome-zarr-vole-tools convert --config batch_config.yaml

# Generate a VolE viewer URL
ome-zarr-vole-tools viewer-url /allen/aics/emt/experiment1.zarr
```

If the entry point is not on your PATH, use:

```bash
python -m ome_zarr_vole_tools convert image.tif -o output/
python -m ome_zarr_vole_tools viewer-url /allen/aics/emt/experiment1.zarr
```

---

## CLI Reference

The tool provides two subcommands: `convert` and `viewer-url`.

```
Usage: ome-zarr-vole-tools [OPTIONS] COMMAND [ARGS]...

  OME-Zarr VolE Tools -- convert microscopy images and generate VolE viewer URLs.

Options:
  --version   Show the version and exit.
  -h, --help  Show this message and exit.

Commands:
  convert     Convert microscopy images to OME-Zarr format.
  viewer-url  Generate VolE viewer URLs from Allen Institute file paths.
```

### convert

```
Usage: ome-zarr-vole-tools convert [OPTIONS] [INPUT_FILES]...
```

Provide `INPUT_FILES` as positional arguments (supports glob patterns), or use `--config` for batch processing via a YAML file. These two modes are mutually exclusive.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `-o`, `--output` | `TEXT` | `.` | Output directory |
| `--config` | `PATH` | — | YAML config file for batch processing |
| `--chunk-size` | `TEXT` | `1,1,64,512,512` | Chunk dimensions as comma-separated ints (TCZYX order) |
| `--pyramid-levels` | `TEXT` | `auto` | Number of pyramid levels, or `auto` to compute from image size |
| `--scale-factor` | `INT` | `2` | Downsampling factor between pyramid levels (2–8) |
| `--compression` | `CHOICE` | `blosc` | Compression algorithm: `blosc`, `zlib`, or `none` |
| `--pixel-sizes` | `TEXT` | — | Physical pixel sizes in microns: `dx,dy` or `dx,dy,dz` |
| `--channel-names` | `TEXT` | — | Comma-separated channel names to override source metadata |
| `--max-workers` | `INT` | `1` | Number of parallel file processing workers |
| `--overwrite` | `FLAG` | `false` | Overwrite existing output `.zarr` directories |

**Examples:**

```bash
# Single file with default settings
ome-zarr-vole-tools convert sample.tif -o zarr_output/

# Multiple files with custom pyramid and compression
ome-zarr-vole-tools convert "experiment/*.nd2" \
    -o zarr_output/ \
    --pyramid-levels 4 \
    --scale-factor 2 \
    --compression zlib \
    --overwrite

# Override pixel sizes and channel names
ome-zarr-vole-tools convert image.czi -o out/ \
    --pixel-sizes 0.65,0.65,2.0 \
    --channel-names "DAPI,GFP,mCherry"

# Parallel batch conversion
ome-zarr-vole-tools convert "data/**/*.tif" -o out/ --max-workers 8 --overwrite
```

**Output structure:**

Each input file produces a `.zarr` directory named after the input file stem:

```
zarr_output/
  sample.zarr/
    .zgroup
    .zattrs          # multiscales + omero metadata
    0/               # full resolution
    1/               # 2x downsampled
    2/               # 4x downsampled
    ...
```

**Auto pyramid levels:**

When `--pyramid-levels auto` (default), the number of levels is computed so that the smallest pyramid level has at least 64 pixels along its smallest spatial dimension. For example, a 512x512 image with scale factor 2 produces 4 levels (512 -> 256 -> 128 -> 64).

**Chunk clamping:**

Requested chunk dimensions are automatically clamped to the actual array shape. If the chunk tuple has fewer dimensions than the array, it is padded with 1s on the left; if it has more, it is trimmed from the left.

### viewer-url

```
Usage: ome-zarr-vole-tools viewer-url [OPTIONS] PATHS...
```

Generates [VolE viewer](https://vol-e.allencell.org) URLs from Allen Institute network paths. Accepts one or more paths and prints one URL per line.

**Supported path formats:**

| Format | Example |
|--------|---------|
| Unix | `/allen/aics/emt/xyz.zarr` |
| Windows UNC | `\\allen\aics\emt\xyz.zarr` |
| Forward-slash UNC | `//allen/aics/emt/xyz.zarr` |

The `/allen/aics/` prefix is stripped and the remainder is appended to `https://vast-files.int.allencell.org/`. The prefix matching is case-insensitive. File extensions are preserved as-is.

**Examples:**

```bash
# Single path
ome-zarr-vole-tools viewer-url /allen/aics/emt/experiment1.zarr
# Output: https://vol-e.allencell.org/viewer?url=https%3A%2F%2Fvast-files.int.allencell.org%2Femt%2Fexperiment1.zarr

# Multiple paths
ome-zarr-vole-tools viewer-url /allen/aics/emt/a.zarr /allen/aics/emt/b.zarr

# Windows UNC path
ome-zarr-vole-tools viewer-url "\\allen\aics\assay-dev\data\image.ome.tiff"
```

---

## YAML Batch Configuration

For complex batch jobs with per-file settings, use a YAML config file. See [`example_config.yaml`](example_config.yaml) for a full example.

```yaml
# Global defaults
output: ./output_zarr
chunk_size: 1,1,64,512,512
pyramid_levels: auto
scale_factor: 2
compression: blosc
max_workers: 4
overwrite: false

# Optional global pixel sizes (microns)
pixel_sizes:
  x: 0.65
  y: 0.65
  z: 2.0

# Files to convert
files:
  # Simple — uses all global defaults
  - path: "data/sample1.tif"

  # Glob pattern
  - path: "data/zeiss/*.czi"

  # Per-file overrides
  - path: "data/special_image.nd2"
    output: ./output_zarr/special
    chunk_size: 1,1,32,256,256
    pyramid_levels: 3
    compression: zlib
    pixel_sizes:
      x: 0.325
      y: 0.325
      z: 1.0
    channel_names:
      - DAPI
      - GFP
      - mCherry
```

Run with:

```bash
ome-zarr-vole-tools convert --config batch_config.yaml
```

CLI flags (`--compression`, `--scale-factor`, `--max-workers`, `--overwrite`, `--chunk-size`) override the corresponding YAML global values when both are provided.

### Schema Reference

**Global settings** (top-level keys):

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `output` | `string` | `"."` | Default output directory |
| `chunk_size` | `list[int]` or `string` | `1,1,64,512,512` | Default chunk dimensions (TCZYX) |
| `pyramid_levels` | `int` or `"auto"` | `"auto"` | Default pyramid level count |
| `scale_factor` | `int` (2–8) | `2` | Default downsampling factor |
| `compression` | `string` | `"blosc"` | `blosc`, `zlib`, or `none` |
| `pixel_sizes` | `{x, y, z?}` | — | Physical pixel sizes in microns |
| `channel_names` | `list[string]` | — | Channel name labels |
| `max_workers` | `int` (>= 1) | `1` | Parallel workers |
| `overwrite` | `bool` | `false` | Replace existing outputs |
| `files` | `list` | *required* | File entries (see below) |

**Per-file entries** (items in `files`):

Each entry can be a plain string (`"path/to/file.tif"`) or a mapping with any of the global settings as overrides:

| Key | Type | Description |
|-----|------|-------------|
| `path` | `string` | *Required.* File path or glob pattern |
| `output` | `string` | Override output directory for this file |
| `chunk_size` | `list[int]` or `string` | Override chunk dimensions |
| `pyramid_levels` | `int` or `"auto"` | Override pyramid levels |
| `scale_factor` | `int` | Override scale factor |
| `compression` | `string` | Override compression |
| `pixel_sizes` | `{x, y, z?}` | Override pixel sizes |
| `channel_names` | `list[string]` | Override channel names |

### Config Merge Logic

For each file, settings are resolved with per-file overrides taking priority:

```
effective_setting = per_file_override ?? global_default
```

This means you can set sensible defaults at the top level and only specify overrides where individual files need different treatment.

---

## Python API

The tool can also be used programmatically:

```python
from pathlib import Path
from ome_zarr_vole_tools.converter import convert_single_file
from ome_zarr_vole_tools.url import build_viewer_url

# Convert a file
zarr_path = convert_single_file(
    input_path=Path("image.tif"),
    output_dir="output/",
    chunk_size=(1, 1, 64, 512, 512),
    pyramid_levels="auto",
    scale_factor=2,
    compression="blosc",
    pixel_sizes={"x": 0.65, "y": 0.65, "z": 2.0},
    channel_names=["DAPI", "GFP"],
    overwrite=True,
)
print(f"Written to {zarr_path}")

# Generate a viewer URL
url = build_viewer_url("/allen/aics/emt/experiment1.zarr")
print(url)
```

### Key functions

| Module | Function | Description |
|--------|----------|-------------|
| `converter` | `convert_single_file()` | End-to-end conversion of one image to OME-Zarr |
| `converter` | `compute_pyramid_levels()` | Calculate pyramid depth from image dimensions |
| `converter` | `adapt_chunk_size()` | Clamp chunk dimensions to array shape |
| `config` | `load_yaml_config()` | Parse and validate a YAML config file |
| `url` | `build_viewer_url()` | Convert an Allen network path to a VolE viewer URL |

---

## Project Structure

```
ome-zarr-vole-tools/
├── pyproject.toml                  # Package metadata, dependencies, entry point
├── example_config.yaml             # Example YAML batch config
├── README.md
├── src/
│   └── ome_zarr_vole_tools/
│       ├── __init__.py             # Package version
│       ├── __main__.py             # python -m support
│       ├── cli.py                  # Click CLI (convert + viewer-url subcommands)
│       ├── config.py               # Pydantic models, YAML loading, validation
│       ├── converter.py            # Core conversion engine
│       ├── url.py                  # VolE viewer URL generation
│       └── utils.py                # Logging setup, Rich summary table
└── tests/
    ├── conftest.py                 # Shared fixtures (synthetic TIFFs, sample YAML)
    ├── test_config.py              # Config model and YAML loading tests
    ├── test_converter.py           # Conversion and helper function tests
    └── test_url.py                 # URL generation tests
```

---

## Development

```bash
# Clone and install in editable mode
git clone <repo-url>
cd ome-zarr-vole-tools
conda create -n ome-zarr-vole-tools python=3.11 -y
conda activate ome-zarr-vole-tools
pip install -e ".[dev]"
```

The `pyproject.toml` is also configured for [uv](https://docs.astral.sh/uv/) if you prefer:

```bash
uv sync
uv run ome-zarr-vole-tools --help
```

---

## Testing

The test suite covers config validation, YAML loading, chunk adaptation, pyramid computation, end-to-end conversion with synthetic TIFFs, overwrite behavior, compression options, metadata writing, and URL generation.

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_converter.py -v

# Run a specific test
python -m pytest tests/test_url.py::TestBuildViewerUrl::test_unix_path -v
```

**Test summary (39 tests):**

| File | Tests | Coverage |
|------|-------|----------|
| `test_config.py` | 14 | Pydantic models, validation rules, YAML parsing, config merging |
| `test_converter.py` | 16 | Pyramid levels, chunk clamping, full conversion, overwrite, compression, metadata |
| `test_url.py` | 9 | Unix/UNC/forward-slash paths, extensions, edge cases, error handling |
