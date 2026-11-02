"""
Geometry and extraction tests.

These use small synthetic rasters with analytically known answers. The point is
to catch the off-by-one and misalignment errors that are invisible in aggregate
statistics but corrupt per-pixel analysis.
"""

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from sarpanel.aoi import load_aoi
from sarpanel.extract import SceneContext, SceneReader, extract_scene, polygon_window


def pixel_measurements(frame):
    """Read a single-scene wide output in tests without changing its pixel rows."""
    return frame.rename(columns={name: name.split("__", 1)[0] for name in frame})


def make_aoi(*geometries, ids=None):
    """Build an AOISet from projected geometries, declaring the CRS explicitly."""
    ids = ids or [f"field_{i}" for i in range(len(geometries))]
    gdf = gpd.GeoDataFrame({"aoi_id": ids}, geometry=list(geometries), crs=CRS)
    return load_aoi(gdf)


CRS = "EPSG:32616"
RES = 10.0
ORIGIN_X, ORIGIN_Y = 500_000.0, 4_500_000.0


def write_band(path, array, nodata=None):
    transform = from_origin(ORIGIN_X, ORIGIN_Y, RES, RES)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=array.shape[0],
        width=array.shape[1],
        count=1,
        dtype="float32",
        crs=CRS,
        transform=transform,
        nodata=nodata,
    ) as dst:
        dst.write(array.astype("float32"), 1)
    return path


@pytest.fixture
def scene(tmp_path):
    """A 100x100 scene with a known 20 dB square patch and a known angle ramp."""
    n = 100
    vv = np.full((n, n), 0.1, dtype=np.float32)
    vv[20:40, 30:50] = 1.0  # bright square, rows 20-39, cols 30-49
    vh = vv * 0.2

    # Incidence angle ramps across range (columns), 30 to 45 degrees.
    inc = np.tile(np.linspace(30.0, 45.0, n, dtype=np.float32), (n, 1))
    lsm = np.zeros((n, n), dtype=np.float32)

    bands = {
        "backscatter": {
            "VV": write_band(tmp_path / "vv.tif", vv),
            "VH": write_band(tmp_path / "vh.tif", vh),
        },
        "inc_angle_projected": write_band(tmp_path / "inc.tif", inc),
        "layover_shadow": write_band(tmp_path / "lsm.tif", lsm),
    }
    return bands


@pytest.fixture
def context():
    return SceneContext(
        scene_id="S1A_TEST",
        acquisition_date="2022-06-15",
        start_time="2022-06-15T23:40:41Z",
        platform="Sentinel-1A",
        orbit_direction="ASCENDING",
        relative_orbit=99,
        absolute_orbit=12345,
        frame_number=500,
        product_type="SLC",
        looks={"looks_nominal": 4.35, "looks_range": 4.35, "looks_azimuth": 1.0},
    )


def polygon_over_bright_patch():
    """Polygon covering exactly the bright square, rows 20-39, cols 30-49."""
    minx = ORIGIN_X + 30 * RES
    maxx = ORIGIN_X + 50 * RES
    maxy = ORIGIN_Y - 20 * RES
    miny = ORIGIN_Y - 40 * RES
    return box(minx, miny, maxx, maxy)


def test_polygon_window_locates_correct_pixels(scene):
    with SceneReader(scene) as reader:
        win = polygon_window(polygon_over_bright_patch(), reader, pad=0)
        assert (int(win.row_off), int(win.col_off)) == (20, 30)
        assert (int(win.height), int(win.width)) == (20, 20)


def test_window_is_clamped_at_raster_edge(scene):
    """A polygon hanging off the corner must not produce negative offsets."""
    edge = box(ORIGIN_X - 500, ORIGIN_Y - 200, ORIGIN_X + 200, ORIGIN_Y + 500)
    with SceneReader(scene) as reader:
        win = polygon_window(edge, reader)
        assert win.row_off >= 0 and win.col_off >= 0
        assert win.row_off + win.height <= reader.shape[0]
        assert win.col_off + win.width <= reader.shape[1]


def test_polygon_entirely_outside_returns_none(scene):
    far = box(ORIGIN_X + 10_000, ORIGIN_Y - 12_000, ORIGIN_X + 11_000, ORIGIN_Y - 11_000)
    with SceneReader(scene) as reader:
        assert polygon_window(far, reader) is None


def test_extracted_values_match_the_synthetic_patch(scene, context, tmp_path):
    aoi = make_aoi(polygon_over_bright_patch())
    with SceneReader(scene) as reader:
        df = pixel_measurements(extract_scene(reader, aoi, context))

    assert len(df) == 400
    assert df["VV_linear"].eq(1.0).all()
    assert df["VV_db"].eq(0.0).all()
    np.testing.assert_allclose(df["VH_db"] - df["VV_db"], 10 * np.log10(0.2), atol=1e-4)


def test_incidence_angle_is_read_from_the_matching_grid(scene, context):
    """
    The angle ramps 30 to 45 across 100 columns. Columns 30-49 therefore span
    a known sub-range, and the mean must match it. This is the test that would
    fail if the angle band came from a different grid than the backscatter.
    """
    aoi = make_aoi(polygon_over_bright_patch())
    with SceneReader(scene) as reader:
        df = pixel_measurements(extract_scene(reader, aoi, context))

    expected = np.linspace(30.0, 45.0, 100)[30:50].mean()
    assert df["inc_angle"].mean() == pytest.approx(expected, abs=1e-3)
    assert df["inc_angle"].min() == pytest.approx(np.linspace(30, 45, 100)[30], abs=1e-3)
    assert df["inc_angle"].max() == pytest.approx(np.linspace(30, 45, 100)[49], abs=1e-3)


def test_misaligned_band_raises_rather_than_silently_resampling(tmp_path, scene):
    """
    A band on a different grid must raise. This is the guard against the
    tie-point-grid failure mode, where an angle array of different dimensions
    is indexed with the output raster's row and column.
    """
    small = np.full((50, 50), 35.0, dtype=np.float32)
    scene = dict(scene)
    scene["inc_angle_projected"] = write_band(tmp_path / "inc_small.tif", small)
    with pytest.raises(ValueError, match="shape"):
        with SceneReader(scene):
            pass


def test_pixel_granularity_returns_one_row_per_pixel(scene, context):
    aoi = make_aoi(polygon_over_bright_patch())
    with SceneReader(scene) as reader:
        df = pixel_measurements(extract_scene(reader, aoi, context))
    assert len(df) == 400
    assert {"x", "y", "VV_db", "inc_angle"} <= set(df.columns)
    assert df["VV_linear"].min() == pytest.approx(1.0)


def test_default_extraction_preserves_pixel_coordinates_and_values(scene, context):
    aoi = make_aoi(polygon_over_bright_patch())
    with SceneReader(scene) as reader:
        df = pixel_measurements(extract_scene(reader, aoi, context))
    assert len(df) == 400
    assert not df.duplicated(["aoi_id", "scene_id", "x", "y"]).any()
    assert sorted(df["x"].unique()) == pytest.approx(
        ORIGIN_X + (np.arange(30, 50) + 0.5) * RES
    )
    assert sorted(df["y"].unique()) == pytest.approx(
        np.sort(ORIGIN_Y - (np.arange(20, 40) + 0.5) * RES)
    )
    expected_angles = np.linspace(30, 45, 100)[30:50]
    by_column = df.groupby("x")["inc_angle"].first().to_numpy()
    np.testing.assert_allclose(by_column, expected_angles, atol=1e-4)
    assert df["VV_db"].eq(0).all()
    assert df["date"].eq(context.acquisition_date).all()
    assert "VV_mean_db" not in df and "enl_empirical" not in df


def test_default_pixel_output_excludes_flagged_pixels(tmp_path, scene, context):
    mask = np.zeros((100, 100), dtype=np.float32)
    mask[20:30, 30:50] = 1
    bands = dict(scene)
    bands["layover_shadow"] = write_band(tmp_path / "flagged.tif", mask)
    with SceneReader(bands) as reader:
        df = pixel_measurements(extract_scene(reader, make_aoi(polygon_over_bright_patch()), context))
    assert len(df) == 200
    assert df["layover_shadow"].eq(0).all()


def test_scene_pipeline_writes_and_aggregates_pixels(tmp_path, scene, monkeypatch):
    from types import SimpleNamespace

    import pandas as pd

    import sarpanel.runner as runner
    from sarpanel.cli import main
    from sarpanel.config import RunConfig

    cfg = RunConfig.from_dict({
        "name": "pixels", "aoi": {"source": "-84.6,42.6,-84.5,42.7"},
        "search": {"start_date": "2020-06-01", "end_date": "2020-06-30"},
        "processing": {"product_type": "GRD"},
        "output": {"root": str(tmp_path / "run")},
    })
    row = pd.Series({
        "scene_id": "one", "acquisition_date": "2020-06-08",
        "start_time": "2020-06-08T00:00:00Z", "url": "unused",
    })
    downloads = []

    def download(url, dest, session):
        downloads.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"synthetic scene")

    monkeypatch.setattr(runner, "download_scene", download)
    monkeypatch.setattr(runner, "geocode_scene", lambda **kwargs: scene)
    aoi = make_aoi(polygon_over_bright_patch())
    caps = SimpleNamespace(supports=lambda name: False)
    result = runner.process_scene(row, aoi, cfg, None, caps, {"config_checksum": cfg.checksum()})
    assert result.status == "ok" and result.n_observations == 400
    df = pd.read_parquet(result.output_path)
    assert len(df) == 400 and {"x", "y"} <= set(df)
    assert any(name.startswith("VV_db__2020-06-08__") for name in df)
    path = cfg.write(tmp_path / "config.yaml")
    assert main(["aggregate", str(path)]) == 0
    assert len(pd.read_parquet(cfg.root / "pixels_observations.parquet")) == 400
    assert runner.process_scene(row, aoi, cfg, None, caps, {}).status == "skipped"
    assert len(downloads) == 1


def test_layover_shadow_pixels_are_excluded(tmp_path, scene, context):
    lsm = np.zeros((100, 100), dtype=np.float32)
    lsm[20:30, 30:50] = 1.0  # flag half the patch as layover
    scene = dict(scene)
    scene["layover_shadow"] = write_band(tmp_path / "lsm2.tif", lsm)

    aoi = make_aoi(polygon_over_bright_patch())
    with SceneReader(scene) as reader:
        df = pixel_measurements(extract_scene(reader, aoi, context))
    assert len(df) == 200
    assert df["layover_shadow"].eq(0).all()


def test_nodata_pixels_are_excluded(tmp_path, context):
    n = 60
    vv = np.full((n, n), 1.0, dtype=np.float32)
    vv[:, :30] = -9999.0
    vh = np.full((n, n), 0.2, dtype=np.float32)
    inc = np.full((n, n), 38.0, dtype=np.float32)
    bands = {
        "backscatter": {
            "VV": write_band(tmp_path / "a.tif", vv, nodata=-9999.0),
            "VH": write_band(tmp_path / "b.tif", vh, nodata=-9999.0),
        },
        "inc_angle_projected": write_band(tmp_path / "c.tif", inc),
    }
    poly = box(ORIGIN_X, ORIGIN_Y - 40 * RES, ORIGIN_X + 40 * RES, ORIGIN_Y)
    aoi = make_aoi(poly)
    with SceneReader(bands) as reader:
        df = pixel_measurements(extract_scene(reader, aoi, context))
    # 40x40 window, but the left 30 columns are nodata -> 40 rows x 10 cols
    assert len(df) == 400
    assert df["VV_linear"].eq(1).all()
