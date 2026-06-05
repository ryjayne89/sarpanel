# sar2parquet

Turn farm-field polygons into analysis-ready **Sentinel-1 SAR** data in **Parquet**.

`sar2parquet` automates a workflow that is normally manual and tedious: given a
set of field boundaries (a GeoPackage / GeoJSON / any GeoDataFrame), it
**searches** the Alaska Satellite Facility (ASF) archive for overlapping
Sentinel-1 scenes over a date range, **downloads** them, **processes** them with
[pyroSAR](https://github.com/johntruckenbrodt/pyroSAR) /
[ESA SNAP](https://step.esa.int/main/toolboxes/snap/) (noise removal,
calibration, terrain correction), and **extracts** the pixels falling inside each
field into compact, columnar Parquet files.

It glues together two tools:

- **[asf_search](https://github.com/asfadmin/Discovery-asf_search)** — find and download Sentinel-1 scenes.
- **[pyroSAR](https://github.com/johntruckenbrodt/pyroSAR)** — drive ESA SNAP to geocode the raw radar data.

---

## What you get

For each acquisition date and orbit direction, one Parquet file with one row per
field pixel:

| column | meaning |
|---|---|
| `farm_name` | field identifier (from your input) |
| `x`, `y` | pixel longitude / latitude (EPSG:4326) |
| `VV` | VV-polarization backscatter (dB by default) |
| `VH` | VH-polarization backscatter (dB by default) |
| `inc_angle` | radar incidence angle at that pixel |

---

## Installation

Because of GDAL-based dependencies (`rasterio`, `geopandas`), the most reliable
setup is a conda environment:

```bash
conda env create -f environment.yml
conda activate sar2parquet
pip install -e .
```

The geocoding step additionally requires **ESA SNAP** installed separately, with
its `gpt` command on your `PATH`. See the
[SNAP download page](https://step.esa.int/main/download/snap-download/).
(Searching and downloading scenes work without SNAP; only the processing step
needs it.)

### Credentials

Downloading from ASF requires a free
[NASA Earthdata](https://urs.earthdata.nasa.gov/) login. Provide credentials in
any of these ways (checked in order):

1. function/CLI arguments (`asf_token=...` or `asf_user=...`, `asf_password=...`),
2. environment variables `ASF_TOKEN` or `ASF_USERNAME` / `ASF_PASSWORD`,
3. a `~/.netrc` entry for `urs.earthdata.nasa.gov`.

---

## Usage

### Python

```python
from sar2parquet import process_fields

out_dir = process_fields(
    fields="my_fields.gpkg",
    start_date="2022-06-01",
    end_date="2022-06-15",
    output_dir="./out",
    asf_token="YOUR_EARTHDATA_TOKEN",
    name_column="farm_name",
)
print("Parquet written to:", out_dir)
```

### Command line

```bash
sar2parquet my_fields.gpkg \
    --start-date 2022-06-01 \
    --end-date 2022-06-15 \
    --output-dir ./out \
    --asf-token YOUR_EARTHDATA_TOKEN
```

Run `sar2parquet --help` for all options (product type, resolution,
polarizations, speckle filtering, area filtering, parallel sharding, etc.).

---

## Notes & limitations

- Sentinel-1 scene processing is resource-intensive (RAM, disk, time). Large
  date ranges or many fields are best run on a workstation or HPC cluster; the
  `--job-index` / `--total-jobs` options allow sharding work across multiple
  workers.
- Defaults: Sentinel-1 **GRD**, **IW** beam mode, 10 m pixels, VV+VH, dB scaling.

## Authors

`sar2parquet` is developed by **Dev Jyoti Ghosh Arnab** and **Ryan Jayne**.

The package is a joint effort to design, generalize, and engineer a Sentinel-1
SAR processing workflow into an installable, reusable, and well-documented tool.

## License

[MIT](LICENSE)
