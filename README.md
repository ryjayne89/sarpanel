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

`sar2parquet` depends on the geospatial stack (`rasterio`, `geopandas`), which is
built on GDAL. **Installing these with plain `pip` often fails on Windows/macOS.**
The reliable path is a dedicated **conda** environment. If you don't have conda,
install [Miniforge](https://github.com/conda-forge/miniforge) (recommended) or
Anaconda first.

These steps assume a completely fresh machine.

### 1. Create and activate an isolated environment

Do **not** install into your base Anaconda environment — mixing the geospatial
stack into base is the most common cause of cryptic `numpy`/GDAL import errors.

```bash
conda create -n sar2parquet -c conda-forge python=3.11 \
    geopandas rasterio shapely pyproj asf_search pyrosar pyarrow ipykernel
conda activate sar2parquet
```

(Equivalently: `conda env create -f environment.yml && conda activate sar2parquet`.)

### 2. Install the package

From PyPI-style source on GitHub:

```bash
pip install --no-deps git+https://github.com/Dev-Arnab/sar2parquet.git
```

`--no-deps` tells pip **not** to re-install the heavy dependencies you already
got from conda (which would risk pulling incompatible pip wheels). To upgrade
later, add `--force-reinstall`.

Or, for local development, clone the repo and run `pip install --no-deps -e .`
from inside it.

Verify the install and version:

```python
import sar2parquet
print(sar2parquet.__version__)
```

### 3. Use it from Jupyter (important kernel step)

If you use Jupyter and your notebook can't find the package (or you get
`numpy` version errors), the cause is almost always that Jupyter is running from
a **different** Python than your `sar2parquet` env. Fix it by registering this
environment as a Jupyter kernel **and selecting it**:

```bash
conda activate sar2parquet
pip install notebook          # or: pip install jupyterlab
python -m ipykernel install --user --name sar2parquet --display-name "Python (sar2parquet)"
jupyter notebook              # launch from INSIDE the activated env
```

Then in the notebook, choose **Kernel → Change kernel → Python (sar2parquet)**.
Sanity-check you're on the right interpreter:

```python
import sys; print(sys.executable)   # should point inside .../envs/sar2parquet/
```

### 4. Install ESA SNAP (only needed for processing)

Searching and downloading scenes work **without** SNAP. The geocoding/processing
step (turning raw scenes into Parquet) needs **ESA SNAP** installed separately,
with its `gpt` command available on your `PATH`.

1. Download and install SNAP from the
   [SNAP download page](https://step.esa.int/main/download/snap-download/)
   (choose the **Sentinel Toolboxes** installer).
2. After installing, confirm `gpt` is reachable:

```bash
gpt -h
```

If `gpt` is "not found", add SNAP's `bin` folder to your `PATH` (e.g. on Windows,
`C:\Program Files\snap\bin`).

---

## Credentials & one-time account setup

Downloading from ASF requires a free **NASA Earthdata** login **and** a one-time
authorization step that trips up almost everyone. Do both:

### A. Create an Earthdata account and token

1. Register at [urs.earthdata.nasa.gov](https://urs.earthdata.nasa.gov/).
2. Log in, then go to your profile → **Generate Token** and copy the token
   string (a long `eyJ...` value).

### B. Authorize the ASF application (required!)

Even with a valid token, your first download will fail with
`Pre authorization required for this application` until you approve ASF's data
app once:

1. Visit [Alaska Satellite Facility Data Access](https://urs.earthdata.nasa.gov/profile)
   → **Applications → Authorized Apps**, or simply trigger a download and open
   the `resolution_url` it prints.
2. Approve **"Alaska Satellite Facility Data Access"**.

This only has to be done once per account.

### C. Provide the credentials to sar2parquet

Credentials are resolved in this order:

1. function/CLI arguments (`asf_token=...`, or `asf_user=...` + `asf_password=...`),
2. environment variables `ASF_TOKEN`, or `ASF_USERNAME` / `ASF_PASSWORD`,
3. a `~/.netrc` entry for `urs.earthdata.nasa.gov`.

> Tip: username/password is often more robust than tokens (tokens expire).
> **Never commit tokens or passwords to git.**

---

## Quickstart

### Make a tiny test field (if you don't have a `.gpkg` yet)

`fields` is just a polygon (or polygons) of the area you want, with a column that
names each field. Here's a one-cell example that creates a small field in Iowa:

```python
import geopandas as gpd
from shapely.geometry import box

gdf = gpd.GeoDataFrame(
    {"farm_name": ["test_field"]},
    geometry=[box(-93.65, 41.95, -93.60, 42.00)],   # lon/lat bounding box
    crs="EPSG:4326",
)
gdf.to_file("my_fields.gpkg", driver="GPKG")
```

The `name_column` argument must match the column holding the field names
(`"farm_name"` above).

### Run the pipeline

```python
from sar2parquet import process_fields

out_dir = process_fields(
    fields="my_fields.gpkg",
    start_date="2025-06-01",
    end_date="2025-06-30",
    output_dir="./out",
    asf_token="YOUR_EARTHDATA_TOKEN",
    name_column="farm_name",
)
print("Output written to:", out_dir)
```

You should see a log line like `[asf] total scenes: N` with `N > 0`, followed by
downloads. Parquet files land in `./out/parquet/`.

### Command line

```bash
sar2parquet my_fields.gpkg \
    --start-date 2025-06-01 \
    --end-date 2025-06-30 \
    --output-dir ./out \
    --asf-token YOUR_EARTHDATA_TOKEN
```

Run `sar2parquet --help` for all options (product type, resolution,
polarizations, speckle filtering, area filtering, parallel sharding, etc.).

---

## Troubleshooting

| Symptom | Cause & fix |
|---|---|
| `ModuleNotFoundError: geopandas` / `rasterio` | The deps aren't in the active env. Create the conda env in step 1 and install the package into it. |
| `numpy` 1.x vs 2.x `ImportError`, or wrong `sys.executable` | Jupyter is using base Anaconda, not your env. Do the kernel step (3) and pick **Python (sar2parquet)**. |
| `jupyter-notebook not found` after `conda install notebook` | Use `pip install notebook` inside the env, then `jupyter notebook`. |
| `ASFAuthenticationError: Invalid/Expired token` | Token has trailing spaces, is truncated, or expired. Re-copy it cleanly, or use username/password instead. |
| `Pre authorization required for this application` | You haven't authorized the ASF app yet. Do credential step **B** above. |
| `ConnectionResetError (10054)` / connection aborted | A firewall/VPN/proxy is blocking NASA Earthdata. Try another network (e.g. phone hotspot) or disable the VPN. |
| `[asf] total scenes: 0` | No Sentinel-1 data for that **location + date range**. Sentinel-1 has a ~6–12 day revisit, and coverage varies by region/year. Widen the range and use **recent dates (e.g. 2025)** — older years may not be in the live archive for all locations. |
| Search works but processing fails | ESA SNAP isn't installed or `gpt` isn't on `PATH`. Do step 4. |

---

## Notes & limitations

- Sentinel-1 scene processing is resource-intensive (RAM, disk, time). Large
  date ranges or many fields are best run on a workstation or HPC cluster; the
  `--job-index` / `--total-jobs` options allow sharding work across multiple
  workers.
- Defaults: Sentinel-1 **GRD**, **IW** beam mode, 10 m pixels, VV+VH, dB scaling.
- Data availability depends on the ASF catalog; coverage and satellites
  (Sentinel-1A/1C/...) vary by region and year.

## Authors

`sar2parquet` is developed by **Dev Jyoti Ghosh Arnab** and **Ryan Jayne**.

The package is a joint effort to design, generalize, and engineer a Sentinel-1
SAR processing workflow into an installable, reusable, and well-documented tool.

## License

[MIT](LICENSE)
