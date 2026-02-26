"""Click CLI entry point for OME-Zarr VolE Tools."""

from __future__ import annotations

import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Tuple

import click
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from .config import ConversionConfig, FileConfig, PhysicalPixelSizes, load_yaml_config
from .converter import convert_single_file, convert_timelapse
from .url import build_viewer_url
from .utils import setup_logging, log_summary

logger = logging.getLogger(__name__)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="ome-zarr-vole-tools")
def cli():
    """OME-Zarr VolE Tools — convert microscopy images and generate VolE viewer URLs."""


# ---------------------------------------------------------------------------
# convert subcommand
# ---------------------------------------------------------------------------


def _parse_chunk_size_str(ctx, param, value: Optional[str]) -> Optional[Tuple[int, ...]]:
    if value is None:
        return None
    try:
        return tuple(int(x.strip()) for x in value.split(","))
    except ValueError:
        raise click.BadParameter("chunk-size must be comma-separated integers, e.g. 1,1,64,512,512")


def _parse_pixel_sizes_str(ctx, param, value: Optional[str]) -> Optional[dict]:
    """Parse 'dx,dy[,dz]' into a dict."""
    if value is None:
        return None
    parts = [x.strip() for x in value.split(",")]
    if len(parts) not in (2, 3):
        raise click.BadParameter("pixel-sizes must be 'dx,dy' or 'dx,dy,dz' in microns")
    try:
        result = {"x": float(parts[0]), "y": float(parts[1])}
        if len(parts) == 3:
            result["z"] = float(parts[2])
        return result
    except ValueError:
        raise click.BadParameter("pixel-sizes must be numeric values in microns")


@cli.command()
@click.argument("input_files", nargs=-1, type=click.Path())
@click.option("-o", "--output", default=".", help="Output directory (default: current dir).")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True),
    default=None,
    help="YAML config file for batch processing.",
)
@click.option(
    "--chunk-size",
    callback=_parse_chunk_size_str,
    default=None,
    help="Chunk size as comma-separated ints (TCZYX order). Default: 1,1,64,512,512.",
)
@click.option(
    "--pyramid-levels",
    default=None,
    help="Number of pyramid levels, or 'auto'. Default: auto.",
)
@click.option(
    "--scale-factor",
    type=int,
    default=None,
    help="Downsampling factor between pyramid levels (2-8). Default: 2.",
)
@click.option(
    "--compression",
    type=click.Choice(["blosc", "zlib", "none"]),
    default=None,
    help="Compression algorithm. Default: blosc.",
)
@click.option(
    "--pixel-sizes",
    callback=_parse_pixel_sizes_str,
    default=None,
    help="Physical pixel sizes in microns: 'dx,dy' or 'dx,dy,dz'.",
)
@click.option(
    "--channel-names",
    default=None,
    help="Comma-separated channel names to override metadata.",
)
@click.option("--max-workers", type=int, default=1, help="Parallel file processing workers. Default: 1.")
@click.option("--overwrite", is_flag=True, default=False, help="Overwrite existing output files.")
def convert(
    input_files: Tuple[str, ...],
    output: str,
    config_path: Optional[str],
    chunk_size: Optional[Tuple[int, ...]],
    pyramid_levels: Optional[str],
    scale_factor: Optional[int],
    compression: Optional[str],
    pixel_sizes: Optional[dict],
    channel_names: Optional[str],
    max_workers: int,
    overwrite: bool,
):
    """Convert microscopy images to OME-Zarr format.

    Provide INPUT_FILES as positional arguments (supports glob patterns),
    or use --config for batch processing via a YAML file.

    \b
    Examples:
      ome-zarr-vole-tools convert image.tif -o output/
      ome-zarr-vole-tools convert "data/*.czi" -o output/ --overwrite
      ome-zarr-vole-tools convert --config batch_config.yaml
    """
    setup_logging()

    # --- Build the ConversionConfig from either YAML or CLI args ---
    if config_path:
        if input_files:
            raise click.UsageError("Cannot use both INPUT_FILES and --config. Choose one.")
        cfg = load_yaml_config(config_path)
        # CLI flags override YAML globals where provided
        if chunk_size is not None:
            cfg.chunk_size = chunk_size
        if scale_factor is not None:
            cfg.scale_factor = scale_factor
        if compression is not None:
            cfg.compression = compression
        if max_workers > 1:
            cfg.max_workers = max_workers
        if overwrite:
            cfg.overwrite = True
    else:
        if not input_files:
            raise click.UsageError("Provide INPUT_FILES or use --config. See --help.")

        # Parse pyramid_levels
        parsed_pyramid = "auto"
        if pyramid_levels is not None:
            if pyramid_levels.lower() == "auto":
                parsed_pyramid = "auto"
            else:
                try:
                    parsed_pyramid = int(pyramid_levels)
                except ValueError:
                    raise click.BadParameter("pyramid-levels must be an integer or 'auto'")

        # Build per-file entries from positional args (each may be a glob)
        file_entries = [FileConfig(path=f) for f in input_files]

        parsed_pixel_sizes = None
        if pixel_sizes:
            parsed_pixel_sizes = PhysicalPixelSizes(**pixel_sizes)

        parsed_channels = None
        if channel_names:
            parsed_channels = [c.strip() for c in channel_names.split(",")]

        cfg = ConversionConfig(
            output=output,
            chunk_size=chunk_size or (1, 1, 64, 512, 512),
            pyramid_levels=parsed_pyramid,
            scale_factor=scale_factor or 2,
            compression=compression or "blosc",
            pixel_sizes=parsed_pixel_sizes,
            channel_names=parsed_channels,
            max_workers=max_workers,
            overwrite=overwrite,
            files=file_entries,
        )

    # --- Resolve all file paths ---
    jobs: list[tuple[Path, dict]] = []
    for fc in cfg.files:
        merged = cfg.merged_config_for_file(fc)
        try:
            paths = fc.resolve_paths()
        except FileNotFoundError as e:
            logger.error(str(e))
            continue
        for p in paths:
            jobs.append((p, merged))

    if not jobs:
        click.echo("No input files found. Nothing to do.", err=True)
        sys.exit(1)

    click.echo(f"Converting {len(jobs)} file(s)...")

    # --- Execute conversions ---
    successes: list[str] = []
    failures: list[tuple[str, str]] = []

    def _run_one(input_path: Path, settings: dict) -> tuple[str, Optional[str]]:
        ps = None
        if settings.get("pixel_sizes"):
            ps_obj = settings["pixel_sizes"]
            ps = {"x": ps_obj.x, "y": ps_obj.y}
            if ps_obj.z is not None:
                ps["z"] = ps_obj.z

        try:
            result = convert_single_file(
                input_path=input_path,
                output_dir=settings["output"],
                chunk_size=settings["chunk_size"],
                pyramid_levels=settings["pyramid_levels"],
                scale_factor=settings["scale_factor"],
                compression=settings["compression"],
                pixel_sizes=ps,
                channel_names=settings.get("channel_names"),
                overwrite=settings["overwrite"],
            )
            return str(result), None
        except Exception as e:
            logger.error("Failed to convert %s: %s", input_path, e)
            return str(input_path), str(e)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
    ) as progress:
        task = progress.add_task("Converting...", total=len(jobs))

        if cfg.max_workers <= 1:
            for input_path, settings in jobs:
                progress.update(task, description=f"Converting {input_path.name}")
                out, err = _run_one(input_path, settings)
                if err is None:
                    successes.append(out)
                else:
                    failures.append((out, err))
                progress.advance(task)
        else:
            with ThreadPoolExecutor(max_workers=cfg.max_workers) as pool:
                futures = {
                    pool.submit(_run_one, ip, s): ip for ip, s in jobs
                }
                for future in as_completed(futures):
                    out, err = future.result()
                    if err is None:
                        successes.append(out)
                    else:
                        failures.append((out, err))
                    progress.advance(task)

    log_summary(successes, failures)

    if failures:
        sys.exit(1)


# ---------------------------------------------------------------------------
# timelapse subcommand
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("input_dir", type=click.Path(exists=True, file_okay=False))
@click.option("-o", "--output", default=".", help="Output directory (default: current dir).")
@click.option("-n", "--name", "output_name", default=None, help="Output zarr name (default: input directory name).")
@click.option("--pattern", default="*.tif*", help="Glob pattern for TIFF files. Default: '*.tif*'.")
@click.option(
    "--chunk-size",
    callback=_parse_chunk_size_str,
    default=None,
    help="Chunk size as comma-separated ints (TCZYX order). Default: 1,1,64,512,512.",
)
@click.option(
    "--pyramid-levels",
    default=None,
    help="Number of pyramid levels, or 'auto'. Default: auto.",
)
@click.option(
    "--scale-factor",
    type=int,
    default=2,
    help="Downsampling factor between pyramid levels (2-8). Default: 2.",
)
@click.option(
    "--compression",
    type=click.Choice(["blosc", "zlib", "none"]),
    default="blosc",
    help="Compression algorithm. Default: blosc.",
)
@click.option(
    "--pixel-sizes",
    callback=_parse_pixel_sizes_str,
    default=None,
    help="Physical pixel sizes in microns: 'dx,dy' or 'dx,dy,dz'.",
)
@click.option(
    "--channel-names",
    default=None,
    help="Comma-separated channel names to override metadata.",
)
@click.option("--overwrite", is_flag=True, default=False, help="Overwrite existing output files.")
def timelapse(
    input_dir: str,
    output: str,
    output_name: Optional[str],
    pattern: str,
    chunk_size: Optional[Tuple[int, ...]],
    pyramid_levels: Optional[str],
    scale_factor: int,
    compression: str,
    pixel_sizes: Optional[dict],
    channel_names: Optional[str],
    overwrite: bool,
):
    """Combine a directory of TIFFs (one per timepoint) into a single timelapse OME-Zarr.

    Files are sorted naturally by filename to determine timepoint order.

    \b
    Examples:
      ome-zarr-vole-tools timelapse /path/to/timepoints/ -o output/
      ome-zarr-vole-tools timelapse /path/to/timepoints/ -n experiment1 --overwrite
      ome-zarr-vole-tools timelapse /path/to/timepoints/ --pattern "*.tiff" --pixel-sizes 0.65,0.65,2.0
    """
    setup_logging()

    parsed_pyramid = "auto"
    if pyramid_levels is not None:
        if pyramid_levels.lower() == "auto":
            parsed_pyramid = "auto"
        else:
            try:
                parsed_pyramid = int(pyramid_levels)
            except ValueError:
                raise click.BadParameter("pyramid-levels must be an integer or 'auto'")

    ps = None
    if pixel_sizes:
        ps = pixel_sizes

    parsed_channels = None
    if channel_names:
        parsed_channels = [c.strip() for c in channel_names.split(",")]

    try:
        result = convert_timelapse(
            input_dir=Path(input_dir),
            output_dir=output,
            output_name=output_name,
            pattern=pattern,
            chunk_size=chunk_size or (1, 1, 64, 512, 512),
            pyramid_levels=parsed_pyramid,
            scale_factor=scale_factor,
            compression=compression,
            pixel_sizes=ps,
            channel_names=parsed_channels,
            overwrite=overwrite,
        )
        click.echo(f"Timelapse OME-Zarr written to: {result}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# viewer-url subcommand
# ---------------------------------------------------------------------------


@cli.command("viewer-url")
@click.argument("paths", nargs=-1, required=True)
def viewer_url(paths: Tuple[str, ...]):
    """Generate VolE viewer URLs from Allen Institute file paths.

    Accepts one or more paths (zarr or tiff, Windows UNC or Unix).

    \b
    Examples:
      ome-zarr-vole-tools viewer-url /allen/aics/emt/xyz.zarr
      ome-zarr-vole-tools viewer-url "\\\\allen\\aics\\emt\\xyz.zarr"
      ome-zarr-vole-tools viewer-url /allen/aics/a.zarr /allen/aics/b.zarr
    """
    for path in paths:
        try:
            url = build_viewer_url(path)
            click.echo(url)
        except ValueError as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)
