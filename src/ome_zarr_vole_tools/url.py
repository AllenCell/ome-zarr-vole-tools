"""Generate VolE viewer URLs from Allen Institute file paths."""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode

VIEWER_BASE = "https://vol-e.allencell.org/viewer"
FILES_BASE = "https://vast-files.int.allencell.org"

# Matches \\allen\aics\... or \allen\aics\... or //allen/aics/... or /allen/aics/...
# Captures everything after the /allen/aics prefix.
_ALLEN_AICS_RE = re.compile(
    r"^(?:\\\\|\\|//|/)allen[/\\]aics[/\\](.+)$",
    re.IGNORECASE,
)


def build_viewer_url(path: str) -> str:
    """Convert an Allen Institute file path to a VolE viewer URL.

    Accepts Windows UNC paths (``\\\\allen\\aics\\...``),
    Unix paths (``/allen/aics/...``), or forward-slash UNC
    (``//allen/aics/...``).

    Parameters
    ----------
    path:
        Path to a zarr store or image file on the Allen network.

    Returns
    -------
    str:
        Full viewer URL, e.g.
        ``https://vol-e.allencell.org/viewer?url=https://vast-files.int.allencell.org/emt/xyz.zarr``

    Raises
    ------
    ValueError:
        If the path does not start with a recognised ``/allen/aics`` prefix.
    """
    match = _ALLEN_AICS_RE.match(path)
    if not match:
        raise ValueError(
            f"Path must start with /allen/aics/ or \\\\allen\\aics\\, got: {path}"
        )

    # Normalise backslashes → forward slashes and strip trailing slashes
    relative = match.group(1).replace("\\", "/").rstrip("/")

    file_url = f"{FILES_BASE}/{relative}"
    return f"{VIEWER_BASE}?{urlencode({'url': file_url})}"
