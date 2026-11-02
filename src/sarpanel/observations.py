"""Wide pixel time series: stable spatial keys and dated acquisition columns."""

from __future__ import annotations

import numpy as np
import pandas as pd

PIXEL_KEYS = ["aoi_id", "x", "y"]


def read_pixel_file(path) -> pd.DataFrame:
    """Read file columns without injecting Hive directory labels such as month."""
    import pyarrow.parquet as pq

    return pq.ParquetFile(path).read().to_pandas()


def validate_pixel_grid(transform, spacing: float) -> None:
    """Require the common zero-origin grid requested from SNAP."""
    if not (
        np.isclose(transform.a, spacing, atol=1e-9, rtol=0)
        and np.isclose(transform.e, -spacing, atol=1e-9, rtol=0)
        and np.isclose(transform.b, 0, atol=1e-9, rtol=0)
        and np.isclose(transform.d, 0, atol=1e-9, rtol=0)
        and np.isclose(transform.c / spacing, round(transform.c / spacing), atol=1e-6, rtol=0)
        and np.isclose(transform.f / spacing, round(transform.f / spacing), atol=1e-6, rtol=0)
    ):
        raise ValueError("SNAP output is not aligned to the common pixel time-series grid")


def canonicalize_pixel_coordinates(frame: pd.DataFrame, spacing: float) -> pd.DataFrame:
    """Remove affine round-off after the scene's standard grid has been checked."""
    frame = frame.copy()
    for coordinate in ("x", "y"):
        cells = np.rint(frame[coordinate].to_numpy() / spacing - 0.5)
        frame[coordinate] = (cells + 0.5) * spacing
    return frame


def date_pixel_columns(frame: pd.DataFrame, date: str, scene_id: str) -> pd.DataFrame:
    """Keep all acquisitions, including distinct granules acquired on one date."""
    suffix = f"__{date}__{scene_id}"
    return frame.rename(columns={
        name: name + suffix for name in frame.columns if name not in PIXEL_KEYS
    })


def acquisition_columns(columns) -> dict[str, list[str]]:
    """Map acquisition keys (date__scene_id) to their measurement columns."""
    acquisitions: dict[str, list[str]] = {}
    for name in columns:
        if name in PIXEL_KEYS:
            continue
        parts = name.split("__", 2)
        if len(parts) != 3 or not parts[2]:
            raise ValueError(f"Expected dated pixel columns; found {name!r}")
        _, date, scene_id = parts
        try:
            pd.Timestamp(date)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid observation date in {name!r}") from exc
        acquisitions.setdefault(f"{date}__{scene_id}", []).append(name)
    return acquisitions


def validate_pixel_columns(columns) -> None:
    columns = list(columns)
    if not set(PIXEL_KEYS) <= set(columns):
        raise ValueError("Pixel output requires aoi_id, x, and y coordinates")
    acquisitions = acquisition_columns(columns)
    if not acquisitions:
        raise ValueError("Pixel output requires dated measurement columns")
    for key, names in acquisitions.items():
        if not any(name.split("__", 1)[0].endswith("_linear") for name in names):
            raise ValueError(f"Acquisition {key!r} has no pixel backscatter")


def combine_pixel_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Outer-join acquisitions on pixels; never average or drop measurements."""
    indexed = []
    seen: set[str] = set()
    for frame in frames:
        validate_pixel_columns(frame.columns)
        if frame.empty:
            continue
        if frame[PIXEL_KEYS].isna().any().any():
            raise ValueError("Pixel identity columns contain missing values")
        if not np.isfinite(frame[["x", "y"]].to_numpy()).all():
            raise ValueError("Pixel coordinates must be finite")
        if frame.duplicated(PIXEL_KEYS).any():
            raise ValueError("A scene file contains duplicate pixel rows")
        measurements = set(frame.columns) - set(PIXEL_KEYS)
        if seen & measurements:
            raise ValueError("Repeated acquisition columns; duplicate scene inputs")
        seen.update(measurements)
        indexed.append(frame.set_index(PIXEL_KEYS))
    if not indexed:
        return pd.DataFrame(columns=PIXEL_KEYS)
    joined = pd.concat(indexed, axis=1, join="outer", verify_integrity=True)
    return joined.sort_index().reset_index()
