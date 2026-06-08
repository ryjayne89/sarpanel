"""Build a search area from field polygons and query the ASF archive."""

from __future__ import annotations

from datetime import datetime
from typing import List

import geopandas as gpd


def build_search_aoi_wkt(fields_gdf: "gpd.GeoDataFrame", buffer_m: float = 10000.0) -> str:
    """Return a WKT polygon covering all fields, optionally buffered.

    The fields are projected to a metric CRS so the buffer is expressed in
    meters, then dissolved into a single geometry and returned in EPSG:4326.
    """
    if buffer_m and buffer_m > 0:
        try:
            crs_m = fields_gdf.estimate_utm_crs()
        except Exception:
            crs_m = "EPSG:3857"
        fields_m = fields_gdf.to_crs(crs_m)
        buffered = fields_m.buffer(buffer_m)
        try:
            aoi = buffered.union_all()
        except Exception:
            aoi = buffered.unary_union
        aoi_ll = gpd.GeoSeries([aoi], crs=crs_m).to_crs("EPSG:4326").iloc[0]
        return aoi_ll.wkt

    gdf_ll = fields_gdf.to_crs("EPSG:4326")
    try:
        return gdf_ll.geometry.union_all().wkt
    except Exception:
        return gdf_ll.unary_union.wkt


def _product_types(names: List[str]) -> List[str]:
    """Resolve product-type names to strings, robust to asf_search versions."""
    import asf_search as asf

    pt = getattr(asf, "PRODUCT_TYPE", None)
    out = []
    for name in names:
        val = getattr(pt, name, None) if pt else None
        out.append(val if isinstance(val, str) else name)
    return out


def search_scenes(
    wkt_aoi: str,
    start_date: str,
    end_date: str,
    product_type: str = "GRD",
    beam_mode: str = "IW",
):
    """Query ASF for Sentinel-1 scenes intersecting ``wkt_aoi``.

    ``product_type`` is ``"GRD"`` (default) or ``"SLC"``. Returns a list of
    ASF product records.
    """
    import asf_search as asf

    if product_type.upper() == "GRD":
        levels = _product_types(["GRD_HD", "GRD_MD"])
    elif product_type.upper() == "SLC":
        levels = _product_types(["SLC"])
    else:
        raise ValueError(f"Unsupported product_type: {product_type!r} (use 'GRD' or 'SLC').")

    kwargs = dict(
        processingLevel=levels,
        start=datetime.strptime(start_date, "%Y-%m-%d").date(),
        end=datetime.strptime(end_date, "%Y-%m-%d").date(),
        intersectsWith=wkt_aoi,
        maxResults=None,
    )

    # Select Sentinel-1 by the constellation-level dataset, which covers all
    # missions (1A/1C/1D, ...). The legacy ``platform=[SENTINEL1A, SENTINEL1B]``
    # filter silently excluded newer satellites (e.g. Sentinel-1C). Fall back to
    # the platform list only on older asf_search versions without DATASET.
    dataset = getattr(getattr(asf, "DATASET", None), "SENTINEL1", None)
    if dataset is not None:
        kwargs["dataset"] = dataset
    else:
        kwargs["platform"] = [asf.PLATFORM.SENTINEL1A, asf.PLATFORM.SENTINEL1B]

    if beam_mode:
        kwargs["beamMode"] = [getattr(asf.BEAMMODE, beam_mode)]

    return list(asf.search(**kwargs))


def strip_product_tags(file_id: str) -> str:
    """Remove product-type suffixes from an ASF fileID."""
    for tag in ("-SLC", "-GRD", "-GRD_HD", "-GRD_MD"):
        file_id = file_id.replace(tag, "")
    return file_id
