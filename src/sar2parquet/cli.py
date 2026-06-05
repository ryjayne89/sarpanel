"""Command-line entry point for sar2parquet."""

from __future__ import annotations

import argparse

from .pipeline import process_fields


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sar2parquet",
        description="Search, download, and process Sentinel-1 SAR over farm "
                    "fields into analysis-ready Parquet files.",
    )
    p.add_argument("fields", help="Path to a vector file of field polygons (.gpkg/.geojson/...).")
    p.add_argument("--start-date", required=True, help="Start date YYYY-MM-DD.")
    p.add_argument("--end-date", required=True, help="End date YYYY-MM-DD.")
    p.add_argument("--output-dir", required=True, help="Directory for outputs.")

    # credentials
    p.add_argument("--asf-user", default=None)
    p.add_argument("--asf-password", default=None)
    p.add_argument("--asf-token", default=None)

    # field handling
    p.add_argument("--name-column", default="farm_name")
    p.add_argument("--area-column", default="acres")
    p.add_argument("--min-area-acres", type=float, default=None)
    p.add_argument("--search-buffer-m", type=float, default=10000.0)

    # search
    p.add_argument("--product-type", default="GRD", choices=["GRD", "SLC"])
    p.add_argument("--beam-mode", default="IW")

    # processing
    p.add_argument("--target-res", type=float, default=10.0)
    p.add_argument("--polarizations", default="all")
    p.add_argument("--speckle-filter", action="store_true", default=False)
    p.add_argument("--java-heap", default="120G")
    p.add_argument("--tmpdir", default=None)

    # parallelism
    p.add_argument("--job-index", type=int, default=0)
    p.add_argument("--total-jobs", type=int, default=1)

    # housekeeping
    p.add_argument("--keep-intermediate", action="store_true", default=False)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    process_fields(
        fields=args.fields,
        start_date=args.start_date,
        end_date=args.end_date,
        output_dir=args.output_dir,
        asf_user=args.asf_user,
        asf_password=args.asf_password,
        asf_token=args.asf_token,
        name_column=args.name_column,
        area_column=args.area_column,
        min_area_acres=args.min_area_acres,
        search_buffer_m=args.search_buffer_m,
        product_type=args.product_type,
        beam_mode=args.beam_mode,
        target_res=args.target_res,
        polarizations=args.polarizations,
        apply_speckle_filter=args.speckle_filter,
        java_heap=args.java_heap,
        tmpdir=args.tmpdir,
        job_index=args.job_index,
        total_jobs=args.total_jobs,
        keep_intermediate=args.keep_intermediate,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
