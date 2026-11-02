"""Pixel defaults, safe migration, and output utilities."""

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sarpanel.cli import main
from sarpanel.config import OutputConfig, RunConfig
from sarpanel.provenance import write_parquet_with_provenance
from sarpanel.runner import output_path_for, process_scene


def config_for(tmp_path, granularity=None):
    output = {"root": str(tmp_path / "run")}
    if granularity:
        output["granularity"] = granularity
    return RunConfig.from_dict({
        "name": "test", "aoi": {"source": "-84.6,42.6,-84.5,42.7"},
        "search": {"start_date": "2020-06-01", "end_date": "2020-06-30"},
        "processing": {"product_type": "GRD"}, "output": output,
    })


def pixels(scene_id="one"):
    return pd.DataFrame({
        "aoi_id": ["field"] * 4, "scene_id": [scene_id] * 4,
        "date": ["2020-06-08"] * 4, "relative_orbit": [48] * 4,
        "x": [500005, 500015, 500005, 500015],
        "y": [4500005, 4500005, 4499995, 4499995],
        "VV_linear": [0.1, 0.2, 0.3, 0.4],
        "VH_linear": [0.01, 0.02, 0.03, 0.04],
        "VV_db": 10 * np.log10([0.1, 0.2, 0.3, 0.4]),
        "VH_db": 10 * np.log10([0.01, 0.02, 0.03, 0.04]),
        "inc_angle": [38, 38.1, 38.2, 38.3],
    })


def save(df, path):
    return write_parquet_with_provenance(df, path, {
        "config_checksum": "test", "aoi_checksum": "aoi",
        "sarpanel_run_name": "test", "software": json.dumps({"snap": "14"}),
    })


def load_tool(name):
    path = Path(__file__).resolve().parents[1] / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_defaults_to_pixel_and_polygon_is_explicit(tmp_path):
    assert OutputConfig(root=str(tmp_path)).granularity == "pixel"
    assert config_for(tmp_path).output.granularity == "pixel"
    assert config_for(tmp_path, "polygon").output.granularity == "polygon"


def test_all_shipped_configs_select_pixels():
    for path in Path("configs").glob("*.yaml"):
        assert RunConfig.from_yaml(path).output.granularity == "pixel", path
    demo = RunConfig.from_yaml("configs/local_demo.yaml")
    assert demo.root == Path("runs/local_demo_pixel")


@pytest.mark.parametrize("existing", ["polygon", "pixel"])
def test_resume_checks_granularity_before_download(tmp_path, monkeypatch, existing):
    import sarpanel.runner as runner

    cfg = config_for(tmp_path)
    scene = pd.Series({"scene_id": "one", "acquisition_date": "2020-06-08"})
    df = pixels()
    if existing == "polygon":
        df = df.iloc[:1][["aoi_id", "scene_id", "date"]].copy()
        df["n_pixels_valid"] = 4
    save(df, output_path_for(cfg, scene))

    def unexpected(*args, **kwargs):
        pytest.fail("Existing output must be inspected before any download")

    monkeypatch.setattr(runner, "download_scene", unexpected)
    result = process_scene(scene, None, cfg, None, None, {})
    if existing == "polygon":
        assert result.status == "failed"
        assert "new output.root" in result.error
    else:
        assert result.status == "skipped" and result.n_observations == 4


def test_aggregate_preserves_all_pixels_from_same_date_scenes(tmp_path, capsys):
    cfg = config_for(tmp_path)
    path = cfg.write(tmp_path / "config.yaml")
    for name in ("one", "two"):
        save(pixels(name), cfg.observations_dir / f"{name}.parquet")
    assert main(["aggregate", str(path)]) == 0
    df = pd.read_parquet(cfg.root / "test_observations.parquet")
    assert len(df) == 8
    assert not df.duplicated(["aoi_id", "scene_id", "x", "y"]).any()
    assert "incidence" in capsys.readouterr().out


def test_aggregate_rejects_old_polygon_files(tmp_path, capsys):
    cfg = config_for(tmp_path)
    path = cfg.write(tmp_path / "config.yaml")
    df = pixels().iloc[:1][["aoi_id", "scene_id", "date"]].copy()
    df["n_pixels_valid"] = 4
    save(df, cfg.observations_dir / "old.parquet")
    assert main(["aggregate", str(path)]) == 1
    assert "new output.root" in capsys.readouterr().out
    assert not (cfg.root / "test_observations.parquet").exists()


def test_check_output_accepts_pixel_schema(tmp_path, monkeypatch, capsys):
    cfg = config_for(tmp_path)
    path = cfg.write(tmp_path / "config.yaml")
    save(pixels(), cfg.observations_dir / "one.parquet")
    monkeypatch.setattr(sys, "argv", ["check_output", str(path)])
    assert load_tool("check_output").main() == 0
    assert "All checks passed" in capsys.readouterr().out


def test_compare_pixel_runs_matches_scene_and_coordinates(tmp_path, monkeypatch, capsys):
    for directory in ("baseline", "candidate"):
        for name in ("one", "two"):
            save(pixels(name), tmp_path / directory / f"{name}.parquet")
    monkeypatch.setattr(sys, "argv", [
        "compare_runs", str(tmp_path / "baseline"), str(tmp_path / "candidate")
    ])
    assert load_tool("compare_runs").main() == 0
    out = capsys.readouterr().out
    assert "matched: 8" in out
    assert "VV_db" in out and "radiometry preserved" in out
