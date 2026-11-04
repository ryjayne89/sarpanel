"""
Pixel-only extraction of Sentinel-1 observations within AOI polygons.

Each scene produces one row per valid pixel. Measurement and acquisition
columns are suffixed with date and scene ID, allowing acquisitions to be
joined side by side without averaging or overwriting same-date granules.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds
from rasterio.windows import transform as window_transform
from shapely.geometry import box

from . import radiometry as rad
from .aoi import AOISet

log = logging.getLogger(__name__)

# Layover/shadow mask bit meanings from SNAP's Terrain Correction operator.
LAYOVER_SHADOW_VALID = 0
LAYOVER_BIT = 1
SHADOW_BIT = 2


@dataclass
class SceneContext:
    """Immutable metadata carried onto every row extracted from one scene."""

    scene_id: str
    acquisition_date: str
    start_time: str
    platform: str
    orbit_direction: str
    relative_orbit: int | None
    absolute_orbit: int | None
    frame_number: int | None
    product_type: str
    looks: dict

    # Measured once per scene, because the equivalent number of looks and the
    # inter-pixel correlation are properties of the sensor and the processing
    # chain rather than of any one polygon. Estimating them per polygon would
    # instead measure that polygon's texture. See radiometry.estimate_scene_enl.
    scene_enl: float | None = None
    scene_enl_homogeneous_fraction: float | None = None
    correlation_inflation: float | None = None

    def as_row_fields(self) -> dict:
        out = {
            "scene_id": self.scene_id,
            "date": self.acquisition_date,
            "start_time": self.start_time,
            "platform": self.platform,
            "orbit_direction": self.orbit_direction,
            "relative_orbit": self.relative_orbit,
            "absolute_orbit": self.absolute_orbit,
            "frame_number": self.frame_number,
            "product_type": self.product_type,
        }
        out.update(
            {
                "looks_nominal": self.looks.get("looks_nominal"),
                "looks_range": self.looks.get("looks_range"),
                "looks_azimuth": self.looks.get("looks_azimuth"),
            }
        )
        # Only present when the optional scene measurement ran. Omitted rather
        # than written as nulls, so the schema says whether the measurement was
        # taken instead of leaving a column that could be mistaken for a
        # failed estimate.
        if self.scene_enl is not None:
            out["scene_enl"] = self.scene_enl
            out["scene_enl_homogeneous_fraction"] = self.scene_enl_homogeneous_fraction
            out["correlation_inflation"] = self.correlation_inflation
        return out


class SceneReader:
    """
    Holds one scene's bands open and serves windowed reads.

    Reading per-polygon windows from an open dataset is faster than loading the
    full scene into memory once the AOI set is sparse, and it removes the
    scene-sized allocation that competes with the JVM for the memory
    allocation. For dense AOI sets covering most of the scene, ``preload``
    reverts to a single full read.
    """

    def __init__(self, bands: dict, preload: bool = False):
        self.paths = bands
        self.preload = preload
        self._datasets: dict[str, rasterio.DatasetReader] = {}
        self._arrays: dict[str, np.ndarray] = {}
        self.transform = None
        self.crs = None
        self.shape = None
        self.nodata = None

    def __enter__(self) -> "SceneReader":
        for pol, path in self.paths["backscatter"].items():
            self._open(f"bs_{pol}", path)
        for role in ("inc_angle_projected", "layover_shadow", "elevation"):
            if self.paths.get(role):
                self._open(role, self.paths[role])

        if not self._datasets:
            raise RuntimeError("No readable bands for this scene")

        reference = next(iter(self._datasets.values()))
        self.transform = reference.transform
        self.crs = reference.crs
        self.shape = (reference.height, reference.width)
        self.nodata = reference.nodata

        self._check_alignment()
        if self.preload:
            for key, ds in self._datasets.items():
                self._arrays[key] = ds.read(1).astype(np.float32)
        return self

    def __exit__(self, *exc):
        for ds in self._datasets.values():
            ds.close()
        self._datasets.clear()
        self._arrays.clear()

    def _open(self, key: str, path) -> None:
        self._datasets[key] = rasterio.open(path)

    def _check_alignment(self) -> None:
        """
        Verify every band shares one grid.

        This is the assertion that makes the export_extra approach safe. If the
        incidence angle band were on a different grid than the backscatter, as
        it is when read from the SNAP tie point grids, this raises instead of
        producing silently misaligned values.
        """
        for key, ds in self._datasets.items():
            if (ds.height, ds.width) != self.shape:
                raise ValueError(
                    f"Band {key!r} has shape {(ds.height, ds.width)} but the "
                    f"reference band has {self.shape}. Bands must share one grid."
                )
            if not _transforms_close(ds.transform, self.transform):
                raise ValueError(
                    f"Band {key!r} has a different geotransform than the "
                    f"reference band. Bands must share one grid."
                )

    def read_window(self, key: str, window: Window) -> np.ndarray | None:
        if key not in self._datasets:
            return None
        if self.preload:
            r0 = int(window.row_off)
            c0 = int(window.col_off)
            return self._arrays[key][
                r0 : r0 + int(window.height), c0 : c0 + int(window.width)
            ]
        return self._datasets[key].read(1, window=window).astype(np.float32)

    @property
    def polarizations(self) -> list[str]:
        return [k[3:] for k in self._datasets if k.startswith("bs_")]


def _transforms_close(a, b, tol: float = 1e-6) -> bool:
    return all(abs(x - y) < tol for x, y in zip(a[:6], b[:6]))


def measure_scene_radiometry(
    reader: SceneReader,
    polarization: str | None = None,
    n_patches: int = 12,
    patch: int = 256,
) -> dict[str, float]:
    """
    Measure the equivalent number of looks and inter-pixel correlation of a scene.

    Both quantities describe the sensor and the processing chain, so they are
    measured once from the raster rather than per polygon. Measuring them per
    polygon would conflate them with that polygon's own spatial structure, and
    the correlation in particular cannot be seen at all from polygon interiors
    alone once the pixels have been written out as a table.

    Patches are sampled on a coarse grid across the scene instead of using the
    whole raster, because the estimators need only enough homogeneous area to
    locate the look count, and reading a full Sentinel-1 scene to do it would
    cost more memory than the rest of extraction combined.

    Parameters
    ----------
    reader
        An open :class:`SceneReader`.
    polarization
        Which band to measure. Defaults to the first available, normally VV,
        which is preferred over VH because it sits further above the noise floor
        and so reflects the processing chain rather than thermal noise.
    n_patches
        Number of patches to sample. Estimates are pooled across patches.
    patch
        Patch side length in pixels.

    Returns
    -------
    dict
        ``scene_enl``, ``scene_enl_homogeneous_fraction`` and
        ``correlation_inflation``. Values are NaN when the scene offers too
        little valid data to measure, which is reported rather than silently
        replaced with a default.
    """
    pols = reader.polarizations
    if not pols:
        return {
            "scene_enl": float("nan"),
            "scene_enl_homogeneous_fraction": float("nan"),
            "correlation_inflation": float("nan"),
        }
    band = f"bs_{(polarization or pols[0]).upper()}"

    height, width = reader.shape
    side = min(patch, height, width)
    per_axis = max(1, int(np.ceil(np.sqrt(n_patches))))

    enl_values: list[float] = []
    homogeneous: list[float] = []
    kappa_values: list[float] = []

    for i in range(per_axis):
        for j in range(per_axis):
            # Space the patches across the interior, away from the swath edges
            # where border noise and incomplete coverage distort the statistics.
            row = int((i + 1) * height / (per_axis + 1)) - side // 2
            col = int((j + 1) * width / (per_axis + 1)) - side // 2
            row = max(0, min(row, height - side))
            col = max(0, min(col, width - side))

            arr = reader.read_window(
                band, Window(col_off=col, row_off=row, width=side, height=side)
            )
            if arr is None:
                continue
            arr = np.asarray(arr, dtype=np.float64)
            if reader.nodata is not None and np.isfinite(reader.nodata):
                arr = np.where(arr == reader.nodata, np.nan, arr)
            if np.isfinite(arr).mean() < 0.5:
                continue

            scene = rad.estimate_scene_enl(arr)
            if np.isfinite(scene.get("enl", np.nan)):
                enl_values.append(scene["enl"])
                homogeneous.append(scene.get("homogeneous_fraction", np.nan))

            kappa = rad.autocorrelation_inflation_factor(arr)
            if np.isfinite(kappa):
                kappa_values.append(kappa)

    # The median across patches resists a single patch that happened to land on
    # water, a built-up area or a scene edge.
    result = {
        "scene_enl": float(np.median(enl_values)) if enl_values else float("nan"),
        "scene_enl_homogeneous_fraction": (
            float(np.nanmedian(homogeneous)) if homogeneous else float("nan")
        ),
        "correlation_inflation": (
            float(np.median(kappa_values)) if kappa_values else float("nan")
        ),
    }
    log.info(
        "Scene radiometry: ENL %.2f (homogeneous fraction %.2f), "
        "correlation inflation %.2f from %d patch(es)",
        result["scene_enl"],
        result["scene_enl_homogeneous_fraction"],
        result["correlation_inflation"],
        len(enl_values),
    )
    return result


def polygon_window(geometry, reader: SceneReader, pad: int = 1) -> Window | None:
    """
    Pixel window covering a polygon, clamped to the raster.

    Uses rasterio's own window arithmetic rather than manual row/column
    algebra. Hand-rolled versions of this calculation tend to be subtly wrong
    at the raster edge or with a negative y resolution, and the error is a
    one-pixel offset that is invisible in aggregate statistics but corrupts
    any per-pixel spatial analysis.
    """
    minx, miny, maxx, maxy = geometry.bounds
    win = from_bounds(minx, miny, maxx, maxy, transform=reader.transform)

    row_off = int(math.floor(win.row_off)) - pad
    col_off = int(math.floor(win.col_off)) - pad
    height = int(math.ceil(win.height)) + 2 * pad
    width = int(math.ceil(win.width)) + 2 * pad

    row_off_clamped = max(0, row_off)
    col_off_clamped = max(0, col_off)
    row_end = min(reader.shape[0], row_off + height)
    col_end = min(reader.shape[1], col_off + width)

    if row_end <= row_off_clamped or col_end <= col_off_clamped:
        return None

    return Window(
        col_off=col_off_clamped,
        row_off=row_off_clamped,
        width=col_end - col_off_clamped,
        height=row_end - row_off_clamped,
    )


def extract_scene(
    reader: SceneReader,
    aoi: AOISet,
    context: SceneContext,
    min_valid_pixels: int = 4,
    drop_layover_shadow: bool = True,
) -> pd.DataFrame:
    """
    Extract every AOI that intersects this scene.

    Returns an empty DataFrame rather than raising when no AOI yields valid
    pixels, since a scene clipping the edge of the AOI set legitimately
    produces nothing.
    """
    aoi_projected = aoi.gdf.to_crs(reader.crs)
    scene_bounds = box(*rasterio.transform.array_bounds(*reader.shape, reader.transform))
    candidates = aoi_projected[aoi_projected.intersects(scene_bounds)]

    log.info(
        "Scene %s: %d of %d AOIs intersect the footprint",
        context.scene_id,
        len(candidates),
        len(aoi_projected),
    )

    rows: list = []
    hit = 0
    for record in candidates.itertuples():
        result = _extract_one(
            reader=reader,
            geometry=record.geometry,
            aoi_id=record.aoi_id,
            context=context,
            min_valid_pixels=min_valid_pixels,
            drop_layover_shadow=drop_layover_shadow,
        )
        if result is not None:
            rows.append(result)
            hit += 1

    log.info("Scene %s: %d AOIs produced valid observations", context.scene_id, hit)
    if not rows:
        return pd.DataFrame()

    return pd.concat(rows, ignore_index=True)


def _extract_one(
    reader: SceneReader,
    geometry,
    aoi_id: str,
    context: SceneContext,
    min_valid_pixels: int,
    drop_layover_shadow: bool,
):
    window = polygon_window(geometry, reader)
    if window is None:
        return None

    win_transform = window_transform(window, reader.transform)
    out_shape = (int(window.height), int(window.width))

    inside = geometry_mask(
        [geometry],
        transform=win_transform,
        out_shape=out_shape,
        invert=True,
        all_touched=False,
    )
    if not inside.any():
        # Polygon smaller than a pixel or falling between pixel centres.
        inside = geometry_mask(
            [geometry],
            transform=win_transform,
            out_shape=out_shape,
            invert=True,
            all_touched=True,
        )
        if not inside.any():
            return None

    pol_arrays: dict[str, np.ndarray] = {}
    valid = inside.copy()
    for pol in reader.polarizations:
        arr = reader.read_window(f"bs_{pol}", window)
        if arr is None:
            continue
        pol_arrays[pol] = arr
        finite = np.isfinite(arr) & (arr > 0)
        if reader.nodata is not None and np.isfinite(reader.nodata):
            finite &= arr != reader.nodata
        # Every polarization must be valid at a pixel for it to be usable,
        # so that co-polar ratios are computed on a consistent sample.
        valid &= finite

    if not pol_arrays:
        return None

    inc = reader.read_window("inc_angle_projected", window)
    if inc is not None:
        valid &= np.isfinite(inc) & (inc > 0)

    lsm = reader.read_window("layover_shadow", window)
    if lsm is not None and drop_layover_shadow:
        valid &= lsm.astype(np.int32) == LAYOVER_SHADOW_VALID

    n_valid = int(valid.sum())
    if n_valid < min_valid_pixels:
        return None

    rows_idx, cols_idx = np.where(valid)
    return _pixel_rows(
        reader, window, rows_idx, cols_idx, pol_arrays, inc, lsm, aoi_id, context
    )


def _pixel_rows(reader, window, rows_idx, cols_idx, pol_arrays, inc, lsm, aoi_id, context):
    abs_rows = rows_idx + int(window.row_off)
    abs_cols = cols_idx + int(window.col_off)
    xs, ys = rasterio.transform.xy(reader.transform, abs_rows, abs_cols)

    data = {
        "aoi_id": aoi_id,
        "x": np.asarray(xs, dtype=np.float64),
        "y": np.asarray(ys, dtype=np.float64),
    }
    for pol, arr in pol_arrays.items():
        linear = arr[rows_idx, cols_idx].astype(np.float32)
        data[f"{pol}_linear"] = linear
        data[f"{pol}_db"] = rad.to_db(linear).astype(np.float32)
    if inc is not None:
        data["inc_angle"] = inc[rows_idx, cols_idx].astype(np.float32)
    if lsm is not None:
        data["layover_shadow"] = lsm[rows_idx, cols_idx].astype(np.int8)

    df = pd.DataFrame(data)
    for key, value in context.as_row_fields().items():
        df[key] = value
    from .observations import date_pixel_columns

    return date_pixel_columns(df, context.acquisition_date, context.scene_id)
