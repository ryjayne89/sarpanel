"""sar2parquet: farm field polygons -> Sentinel-1 SAR -> analysis-ready Parquet.

Combines ASF Search (scene discovery + download) with pyroSAR/SNAP (geocoding)
and writes compact, per-pixel Parquet for each field and acquisition date.
"""

from __future__ import annotations

__version__ = "0.1.1"

__all__ = ["process_fields", "__version__"]


def __getattr__(name):
    # Lazily import the heavy orchestrator so that `import sar2parquet` and
    # lightweight submodules work without the full geospatial stack installed.
    if name == "process_fields":
        from .pipeline import process_fields
        return process_fields
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
