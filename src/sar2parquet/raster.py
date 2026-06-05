"""Small raster helpers: dB conversion, ENVI reading, and metadata sniffing."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np


def convert_linear_tif_to_db(tif_path: Path) -> Path:
    """Convert a linear-scale GeoTIFF to decibels, written as ``*_db.tif``."""
    import rasterio

    tif_path = Path(tif_path)
    db_path = tif_path.with_name(tif_path.stem + "_db.tif")
    with rasterio.open(tif_path) as src:
        data = src.read().astype(np.float32)
        meta = src.meta.copy()
    data_db = np.where(data > 0, 10.0 * np.log10(data), np.nan)
    meta.update(dtype=rasterio.float32, nodata=np.nan)
    with rasterio.open(db_path, "w", **meta) as dst:
        dst.write(data_db)
    return db_path


def read_envi_img(hdr_path: Path) -> np.ndarray:
    """Read an ENVI ``.img`` raster given its ``.hdr`` header path."""
    hdr_path = Path(hdr_path)
    hdr = {}
    with open(hdr_path, "r") as f:
        for line in f:
            if "=" in line:
                k, v = line.split("=", 1)
                hdr[k.strip().lower()] = v.strip()

    lines = int(hdr.get("lines", 0))
    samples = int(hdr.get("samples", 0))
    dtype_code = int(hdr.get("data type", 4))
    byte_order = int(hdr.get("byte order", 0))
    dtype_map = {1: np.uint8, 2: np.int16, 3: np.int32,
                 4: np.float32, 5: np.float64, 12: np.uint16}
    dtype = dtype_map.get(dtype_code, np.float32)

    img_path = hdr_path.with_suffix(".img")
    data = np.fromfile(str(img_path), dtype=dtype).reshape(lines, samples)
    if byte_order == 1:
        data = data.byteswap()
    return data


def find_incidence_angle_grid(tmpdir: str) -> Optional[Path]:
    """Locate the SNAP incidence-angle tie-point grid under ``tmpdir``."""
    base = Path(tmpdir)
    patterns = [
        "*_Cal_NR_Orb_TF_TC_dB*/sub/*.data/tie_point_grids/incident_angle.hdr",
        "*_Cal_NR_Orb_TF_TC_dB/sub/*.data/tie_point_grids/incident_angle.hdr",
    ]
    for pat in patterns:
        hits = sorted(base.glob(pat))
        if hits:
            return hits[0]
    return None


def extract_orbit_dir(scene_outdir: Path) -> str:
    """Infer orbit direction (ASCENDING/DESCENDING) from output TIF filenames."""
    tifs = sorted(Path(scene_outdir).glob("*.tif"))
    if tifs:
        for part in tifs[0].stem.split("_"):
            if part == "A":
                return "ASCENDING"
            if part == "D":
                return "DESCENDING"
    return "UNKNOWN"
