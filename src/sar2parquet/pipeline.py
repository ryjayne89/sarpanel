"""End-to-end orchestration: fields + date range -> Sentinel-1 SAR Parquet.

This is the public entry point. It generalizes the original cluster script:
hard-coded paths, the ``acres >= 3`` filter, and the SLURM job-sharding are now
optional parameters that default to sensible, laptop-friendly values.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd
import geopandas as gpd

from .auth import get_creds, build_asf_session
from .search import build_search_aoi_wkt, search_scenes, strip_product_tags
from .download import product_download_url, download_with_asf_session
from .geocode import prep_env, geocode_scene
from .raster import (
    convert_linear_tif_to_db,
    read_envi_img,
    find_incidence_angle_grid,
    extract_orbit_dir,
)
from .extract import extract_field_pixels_from_arrays


def _load_fields(fields: Union[str, Path, "gpd.GeoDataFrame"]) -> "gpd.GeoDataFrame":
    if isinstance(fields, gpd.GeoDataFrame):
        return fields
    return gpd.read_file(str(fields))


def process_fields(
    fields: Union[str, Path, "gpd.GeoDataFrame"],
    start_date: str,
    end_date: str,
    output_dir: Union[str, Path],
    *,
    # credentials
    asf_user: Optional[str] = None,
    asf_password: Optional[str] = None,
    asf_token: Optional[str] = None,
    # field handling
    name_column: str = "farm_name",
    area_column: str = "acres",
    min_area_acres: Optional[float] = None,
    search_buffer_m: float = 10000.0,
    # search
    product_type: str = "GRD",
    beam_mode: str = "IW",
    # processing
    target_res: float = 10.0,
    polarizations: str = "all",
    apply_speckle_filter: bool = False,
    java_heap: str = "120G",
    tmpdir: Optional[str] = None,
    # parallelism (optional sharding across workers/nodes)
    job_index: int = 0,
    total_jobs: int = 1,
    # housekeeping
    keep_intermediate: bool = False,
) -> Path:
    """Search, download, geocode, and extract Sentinel-1 SAR to Parquet.

    Parameters
    ----------
    fields
        Path to a vector file (``.gpkg``/``.geojson``/...) or a GeoDataFrame.
        Each row is one field; a polygon geometry and a name column are required.
    start_date, end_date
        Inclusive date bounds as ``"YYYY-MM-DD"``.
    output_dir
        Root directory for outputs. Parquet files land in ``<output_dir>/parquet``;
        working files live under ``<output_dir>/_work`` and are removed unless
        ``keep_intermediate`` is set.
    min_area_acres
        If given, fields smaller than this (per ``area_column``) are dropped.
    job_index, total_jobs
        Optional sharding for running across multiple workers; defaults run all
        scenes in a single process.

    Returns
    -------
    Path
        The directory containing the written per-scene Parquet files.
    """
    tmpdir = prep_env(java_heap=java_heap, tmpdir=tmpdir)
    user, pwd, token = get_creds(asf_user, asf_password, asf_token)
    asf_sess = build_asf_session(user, pwd, token)

    fields_gdf = _load_fields(fields)
    if name_column not in fields_gdf.columns:
        raise ValueError(
            f"Field name column {name_column!r} not found. "
            f"Available columns: {list(fields_gdf.columns)}"
        )

    if min_area_acres is not None:
        if area_column not in fields_gdf.columns:
            raise ValueError(
                f"Area column {area_column!r} not found but min_area_acres was set."
            )
        pre = len(fields_gdf)
        fields_gdf = fields_gdf[fields_gdf[area_column] >= min_area_acres].reset_index(drop=True)
        print(f"[fields] {pre} -> {len(fields_gdf)} fields after >= {min_area_acres} {area_column} filter")
    if len(fields_gdf) == 0:
        raise RuntimeError("No fields remain to process.")

    base = Path(output_dir)
    download_dir = base / "_work" / "downloads"
    processed_dir = base / "_work" / "processed_S1"
    parquet_dir = base / "parquet"
    for d in (download_dir, processed_dir, parquet_dir):
        d.mkdir(parents=True, exist_ok=True)

    scaling = "linear" if apply_speckle_filter else "dB"
    print(f"[config] product={product_type} beam={beam_mode} "
          f"speckle_filter={apply_speckle_filter} scaling={scaling}")

    wkt_aoi = build_search_aoi_wkt(fields_gdf, buffer_m=search_buffer_m)
    print("[asf] querying scenes ...")
    results = search_scenes(wkt_aoi, start_date, end_date,
                            product_type=product_type, beam_mode=beam_mode)
    print(f"[asf] total scenes: {len(results)}")
    if not results:
        return parquet_dir

    my_scenes = np.array_split(results, total_jobs)[job_index]
    print(f"[job {job_index}/{total_jobs}] assigned {len(my_scenes)} scenes")

    farms_ll = fields_gdf.to_crs("EPSG:4326")

    for scene in my_scenes:
        _process_one_scene(
            scene=scene,
            farms_ll=farms_ll,
            name_column=name_column,
            asf_sess=asf_sess,
            download_dir=download_dir,
            processed_dir=processed_dir,
            parquet_dir=parquet_dir,
            tmpdir=tmpdir,
            target_res=target_res,
            polarizations=polarizations,
            scaling=scaling,
            apply_speckle_filter=apply_speckle_filter,
            start_date=start_date,
            keep_intermediate=keep_intermediate,
        )

    return parquet_dir


def _process_one_scene(
    *, scene, farms_ll, name_column, asf_sess, download_dir, processed_dir,
    parquet_dir, tmpdir, target_res, polarizations, scaling, apply_speckle_filter,
    start_date, keep_intermediate,
):
    scene_id = strip_product_tags(scene.properties.get("fileID", "unknown"))
    date_str = (scene.properties.get("startTime") or "")[:10].replace("-", "")
    if not date_str:
        date_str = start_date.replace("-", "")
    print(f"\n[scene] {scene_id}  date={date_str}")

    dl_url = product_download_url(scene)
    if not dl_url:
        print("  no download URL; skipping")
        return
    zip_path = download_dir / f"{scene_id}.zip"
    if zip_path.exists() and zip_path.stat().st_size > 0:
        print(f"  ZIP exists: {zip_path.name}")
    else:
        print(f"  downloading: {zip_path.name}")
        try:
            download_with_asf_session(dl_url, zip_path, asf_sess)
        except Exception as e:
            print(f"  download failed: {e}")
            return

    scene_outdir = processed_dir / scene_id
    scene_outdir.mkdir(parents=True, exist_ok=True)
    try:
        geocode_scene(
            infile=str(zip_path),
            outdir=str(scene_outdir),
            tmpdir=tmpdir,
            target_res=target_res,
            polarizations=polarizations,
            scaling=scaling,
            speckle_filter=apply_speckle_filter,
        )
        print("  geocode done")
    except Exception as e:
        print(f"  geocode failed: {e}")
        return

    if not keep_intermediate:
        try:
            zip_path.unlink()
        except Exception:
            pass

    tifs = list(scene_outdir.rglob("*.tif"))
    if not tifs:
        print("  no GeoTIFFs found")
        return

    if apply_speckle_filter:
        converted = []
        for t in tifs:
            if "_db.tif" in t.name:
                converted.append(t)
                continue
            try:
                db_path = convert_linear_tif_to_db(t)
                t.unlink()
                converted.append(db_path)
            except Exception as e:
                print(f"    dB conversion failed: {e}")
        tifs = converted

    vv_tif = next((t for t in tifs if "_VV_" in t.name), None)
    vh_tif = next((t for t in tifs if "_VH_" in t.name), None)
    if not vv_tif or not vh_tif:
        print("  could not find VV and VH tifs")
        return

    orbit_dir = extract_orbit_dir(scene_outdir)
    out_parquet = parquet_dir / f"{date_str}_{orbit_dir}.parquet"
    if out_parquet.exists():
        print(f"  parquet already exists, skipping: {out_parquet.name}")
        if not keep_intermediate:
            for t in tifs:
                try:
                    t.unlink()
                except Exception:
                    pass
        return

    inc_grid = None
    inc_hdr = find_incidence_angle_grid(tmpdir)
    if inc_hdr:
        try:
            inc_grid = read_envi_img(inc_hdr)
        except Exception as e:
            print(f"  incidence angle load failed: {e}")

    import rasterio
    try:
        with rasterio.open(vv_tif) as src_vv:
            vv_full = src_vv.read(1).astype(np.float32)
            transform = src_vv.transform
            src_shape = (src_vv.height, src_vv.width)
            nodata = src_vv.nodata if src_vv.nodata is not None else np.nan
        with rasterio.open(vh_tif) as src_vh:
            vh_full = src_vh.read(1).astype(np.float32)
    except Exception as e:
        print(f"  failed to read TIF arrays: {e}")
        return

    if not np.isnan(nodata):
        vv_full[vv_full == nodata] = np.nan
        vh_full[vh_full == nodata] = np.nan

    all_rows = []
    hit = missed = 0
    clip_start = time.time()
    for _, farm_row in farms_ll.iterrows():
        farm_name = str(farm_row.get(name_column, "unknown")).replace(" ", "_")
        geom = farm_row["geometry"].__geo_interface__
        result = extract_field_pixels_from_arrays(
            vv_full=vv_full,
            vh_full=vh_full,
            inc_grid=inc_grid,
            transform=transform,
            nodata=nodata,
            farm_name=farm_name,
            geom_geo_interface=geom,
            src_shape=src_shape,
        )
        if result is not None:
            all_rows.append(result)
            hit += 1
        else:
            missed += 1
    print(f"  extraction done in {time.time() - clip_start:.1f}s ({hit} hit, {missed} missed)")

    del vv_full, vh_full

    if all_rows:
        scene_df = pd.concat(all_rows, ignore_index=True)
        scene_df.to_parquet(out_parquet, index=False, compression="zstd")
        print(f"  wrote {out_parquet.name} "
              f"({len(scene_df):,} rows, {hit} fields, "
              f"{out_parquet.stat().st_size / 1024 / 1024:.1f} MB)")
        del scene_df
    else:
        print("  no field data extracted for this scene")

    if not keep_intermediate:
        for t in tifs:
            try:
                t.unlink()
            except Exception:
                pass
