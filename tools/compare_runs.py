#!/usr/bin/env python3
"""Compare wide pixel time series on identical pixel and acquisition keys."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def load(directory):
    from sarpanel.observations import combine_pixel_frames, read_pixel_file

    files = sorted(Path(directory).rglob("*.parquet"))
    if not files:
        raise ValueError(f"No pixel files under {directory}")
    return combine_pixel_frames([read_pixel_file(file) for file in files])


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from sarpanel.observations import PIXEL_KEYS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("--labels", nargs=2, default=["baseline", "candidate"])
    parser.add_argument("--tolerance", type=float, default=0.05)
    args = parser.parse_args()
    try:
        a, b = load(args.baseline), load(args.candidate)
    except (ValueError, OSError) as exc:
        print(f"Cannot compare: {exc}")
        return 1
    a, b = a.set_index(PIXEL_KEYS).sort_index(), b.set_index(PIXEL_KEYS).sort_index()
    if not a.index.equals(b.index):
        print("FAIL: pixel coverage differs between runs")
        return 1
    if set(a.columns) != set(b.columns):
        print("FAIL: acquisition or measurement columns differ between runs")
        return 1
    print(f"{args.labels[0]}: {len(a):,} pixels; {args.labels[1]}: {len(b):,} pixels; "
          f"matched: {len(a):,}")
    ok = True
    for name in a.columns:
        if not a[name].isna().equals(b[name].isna()):
            print(f"FAIL: missing-observation pattern differs in {name}")
            ok = False
            continue
        if not pd.api.types.is_numeric_dtype(a[name]):
            if not a[name].fillna("").equals(b[name].fillna("")):
                print(f"FAIL: acquisition metadata differs in {name}")
                ok = False
            continue
        observed = a[name].notna()
        diff = (b.loc[observed, name] - a.loc[observed, name]).to_numpy(dtype=float)
        if not len(diff):
            continue
        if not np.isfinite(diff).all():
            ok = False
            continue
        field = name.split("__", 1)[0]
        if field.endswith("_db"):
            offset = float(diff.mean())
            passed = abs(offset) <= args.tolerance
            print(f"{'PASS' if passed else 'FAIL'} {name}: mean offset {offset:+.4f} dB; "
                  f"max absolute {np.abs(diff).max():.4f} dB")
            ok &= passed
    print("VERDICT: radiometry preserved within tolerance." if ok else
          "VERDICT: pixel coverage, metadata, or radiometry changed.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
