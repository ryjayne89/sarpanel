"""Extract per-pixel SAR values for a single field from full-scene arrays."""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def extract_field_pixels_from_arrays(
    vv_full: np.ndarray,
    vh_full: np.ndarray,
    inc_grid: Optional[np.ndarray],
    transform,
    nodata: float,
    farm_name: str,
    geom_geo_interface: dict,
    src_shape: tuple,
) -> Optional[pd.DataFrame]:
    """Return a tidy DataFrame of pixels falling inside one field polygon.

    Works on in-memory VV/VH arrays (already read once per scene) and windows
    to the polygon's bounding box to avoid scanning the whole scene per field.
    Columns: ``farm_name, x, y, VV, VH, inc_angle``.
    """
    import rasterio
    from rasterio.transform import rowcol, xy
    from rasterio.features import geometry_mask
    from shapely.geometry import shape

    try:
        geom_shape = shape(geom_geo_interface)

        bounds = geom_shape.bounds  # (minx, miny, maxx, maxy)
        row_min, col_min = rowcol(transform, bounds[0], bounds[3])  # top-left
        row_max, col_max = rowcol(transform, bounds[2], bounds[1])  # bottom-right

        row_min = max(0, min(row_min, src_shape[0] - 1))
        row_max = max(0, min(row_max + 1, src_shape[0]))
        col_min = max(0, min(col_min, src_shape[1] - 1))
        col_max = max(0, min(col_max + 1, src_shape[1]))

        if row_min >= row_max or col_min >= col_max:
            return None

        vv_win = vv_full[row_min:row_max, col_min:col_max]
        vh_win = vh_full[row_min:row_max, col_min:col_max]

        win_transform = rasterio.transform.from_bounds(
            *rasterio.transform.array_bounds(
                row_max - row_min, col_max - col_min,
                rasterio.transform.from_origin(
                    transform.c + col_min * transform.a,
                    transform.f + row_min * transform.e,
                    transform.a, -transform.e,
                ),
            ),
            col_max - col_min,
            row_max - row_min,
        )

        field_mask = geometry_mask(
            [geom_geo_interface],
            transform=win_transform,
            invert=True,  # True = inside field
            out_shape=(row_max - row_min, col_max - col_min),
        )

        valid_mask = field_mask & ~np.isnan(vv_win) & (vv_win != nodata)
        rows_idx, cols_idx = np.where(valid_mask)

        if len(rows_idx) == 0:
            return None

        abs_rows = rows_idx + row_min
        abs_cols = cols_idx + col_min
        xs, ys = xy(transform, abs_rows, abs_cols)

        vv_vals = vv_win[rows_idx, cols_idx].astype(np.float32)
        vh_vals = vh_win[rows_idx, cols_idx].astype(np.float32)

        if inc_grid is not None:
            ir = np.clip(abs_rows, 0, inc_grid.shape[0] - 1)
            ic = np.clip(abs_cols, 0, inc_grid.shape[1] - 1)
            inc_vals = inc_grid[ir, ic].astype(np.float32)
        else:
            inc_vals = np.full(len(vv_vals), np.nan, dtype=np.float32)

        n = len(vv_vals)
        return pd.DataFrame({
            "farm_name": [farm_name] * n,
            "x": np.array(xs, dtype=np.float64),
            "y": np.array(ys, dtype=np.float64),
            "VV": vv_vals,
            "VH": vh_vals,
            "inc_angle": inc_vals,
        })

    except Exception as e:
        print(f"    pixel extraction error for {farm_name}: {e}")
        return None
