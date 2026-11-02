#!/usr/bin/env python3
"""Validate pixel-only time-series outputs without collapsing spatial data."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np


def bootstrap():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("--parquet", help="check one scene or combined file")
    args = parser.parse_args()
    bootstrap()
    from sarpanel.config import RunConfig
    from sarpanel.observations import acquisition_columns, combine_pixel_frames, read_pixel_file
    from sarpanel.provenance import read_provenance

    config = RunConfig.from_yaml(args.config)
    files = [Path(args.parquet)] if args.parquet else sorted(
        config.observations_dir.rglob("*.parquet")
    )
    if not files:
        print(f"No pixel outputs under {config.observations_dir}")
        return 1
    try:
        frame = combine_pixel_frames([read_pixel_file(file) for file in files])
    except (ValueError, OSError) as exc:
        print(f"FAIL: {exc}")
        return 1
    acquisitions = acquisition_columns(frame.columns)
    print(f"Pixel rows: {len(frame):,}; AOIs: {frame.aoi_id.nunique():,}; "
          f"acquisitions: {len(acquisitions):,}")
    ok = True
    for key, names in acquisitions.items():
        columns = {name.split("__", 1)[0]: name for name in names}
        linear = [columns.get(f"{pol}_linear") for pol in config.processing.polarizations]
        if any(name is None for name in linear):
            print(f"FAIL: missing polarization in {key}")
            ok = False
            continue
        observed = frame[linear].notna().any(axis=1)
        valid = bool(observed.any())
        for pol, name in zip(config.processing.polarizations, linear):
            values = frame.loc[observed, name].to_numpy(dtype=float)
            valid &= bool(np.isfinite(values).all() and (values > 0).all())
            db = columns.get(f"{pol}_db")
            if db is None:
                valid = False
            else:
                with np.errstate(divide="ignore", invalid="ignore"):
                    valid &= bool(np.allclose(
                        frame.loc[observed, db], 10 * np.log10(values),
                        atol=1e-4, equal_nan=False,
                    ))
        angle = columns.get("inc_angle")
        if angle is None:
            valid = False
        else:
            angles = frame.loc[observed, angle].to_numpy(dtype=float)
            valid &= bool(np.isfinite(angles).all() and (angles > 0).all())
        mask = columns.get("layover_shadow")
        if mask and config.output.drop_layover_shadow:
            valid &= bool(frame.loc[observed, mask].eq(0).all())
        print(f"{'PASS' if valid else 'FAIL'} {key}: {int(observed.sum()):,} pixels")
        ok &= valid
    for file in files:
        provenance = read_provenance(file)
        recorded = all(key in provenance for key in (
            "config_checksum", "aoi_checksum", "sarpanel_run_name"
        ))
        if not recorded:
            print(f"FAIL: incomplete provenance in {file}")
        ok &= recorded
    print("All checks passed." if ok else "One or more pixel checks FAILED.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
