# sarpanel

Analysis-ready **Sentinel-1 pixel time series**, written to **Parquet**,
with spatial coordinates, backscatter, and acquisition geometry retained.

Give the program an area of interest (one polygon or many) and a date range.
It queries the Alaska Satellite Facility (ASF) archive, processes matching
Sentinel-1 scenes with [ESA SNAP](https://step.esa.int/main/toolboxes/snap/)
through [pyroSAR](https://github.com/johntruckenbrodt/pyroSAR), and writes
**one row per pixel per AOI, with dated columns for each acquisition**.
Each scene file contains that scene's pixel measurements; `aggregate` joins
acquisitions side by side on the pixel coordinates. Polygons identify the area
to extract. They are never averaged into one row.

The polygons can be anything with a boundary. Nothing downstream inspects what
a polygon represents, so a run over one polygon and a run over 300,000 differ
only in how many rows come out. Accepted inputs are any vector file GeoPandas
can read, a WKT string, a bounding box written as `minx,miny,maxx,maxy`, or a
list of bounding boxes.

## What this is for

Sentinel-1 has imaged the entire land surface every 6 to 12 days since 2014,
through cloud and darkness, and the archive is free. The obstacle has never been
the data. It is that turning a decade of radar scenes into a table you can model
takes a working SNAP installation, a catalog search that does not silently drop
scenes, correct handling of terrain and radiometry, and a way to keep track of
which pixel came from where under which viewing geometry. `sarpanel` is that
middle layer.

Because it makes no assumption about what the polygons are, the same pipeline
serves problems that are usually handled by separate tooling.

| Domain | What the polygons are | What the time series supports |
| --- | --- | --- |
| Agriculture | Parcels, management zones, trial plots | Crop type, phenology, biomass accumulation, harvest and tillage dates, within-parcel variability |
| Land use and land cover change | Administrative units, concession boundaries, sample frames | Forest loss and regrowth, conversion to cropland, wetland drainage, encroachment |
| Infrastructure | Building footprints, road and rail corridors, dams, pipelines, port facilities | Construction progress, subsidence-prone surfaces, activity at facilities, change after an event |
| Water and cryosphere | Reservoirs, floodplains, river reaches, glacier termini, sea ice zones | Inundation extent and timing, reservoir drawdown, melt onset, terminus retreat |
| Hazards and response | Burn perimeters, landslide scars, earthquake-affected districts | Damage proxy mapping, pre-event baselines, recovery trajectories |
| Mining and extraction | Pit boundaries, tailings facilities, well pads | Footprint expansion, surface disturbance, activity monitoring |
| Calibration and method work | Corner reflector sites, homogeneous reference areas | Radiometric stability, look-count verification, speckle characterization |

Radar is well suited to all of these for the same reason: it measures surface
geometry, roughness, and moisture rather than reflected sunlight, so it sees
through cloud, works at night, and responds to physical structure. The cost is
that it needs more careful handling than optical imagery, which is most of what
this package does for you.

### What it does that a raster pipeline does not

Several good tools already produce calibrated Sentinel-1 rasters, among them
HyP3, pyroSAR itself, and the OPERA RTC products. They stop at imagery and leave
you to sample it, which is where acquisition metadata usually gets discarded.

`sarpanel` ends at a table instead, and every row keeps the conditions it was
measured under.

- **Per-pixel coordinates on a fixed, snapped grid**, so a pixel is the same
  pixel across years and the time series is a real panel rather than a stack of
  near-misses.
- **Projected local incidence angle per acquisition**, taken from the
  terrain-correction output band rather than interpolated from tie points.
  Backscatter depends on viewing angle, and observations from different orbit
  tracks arrive at angles that can differ by 9 degrees or more, so an analysis
  that ignores this is fitting geometry as if it were signal.
- **Orbit track, platform, and timing**, so observations can be conditioned on
  viewing geometry instead of pooled blindly.
- **Layover and shadow masks**, which mark pixels that are not measurements at
  all.
- **The pieces needed to quantify precision**, covered under
  [Looks, speckle, and precision](#looks-speckle-and-precision). Polygon size
  and processing history determine how reliable an estimate is, and the package
  records what is needed to weight observations accordingly rather than treating
  them as equally informative.
- **Provenance in every file**, including software versions, the DEM used, and
  the full run configuration, so a result stays traceable after the file leaves
  the directory it was written in.
- **No averaging inside polygons.** Polygons say where to extract, not what to
  collapse, so within-polygon spatial structure survives for sub-polygon
  analysis. Aggregate later if you want to, with the tools in
  `sarpanel.radiometry`.

### Scale it has been run at

The pipeline has processed a full Sentinel-1 SLC archive over a 125 by 180 km
area: 692 scenes spanning 2016 to 2023 across two orbit tracks, 327 polygons,
347 distinct acquisition dates, and roughly 259 million pixel observations,
with 691 of 692 scenes completing. It runs on a laptop for a single polygon and
on a SLURM array for an archive, from the same configuration file.

This tool was previously called `sar2parquet`. The repository, Python import,
and command-line program are now named `sarpanel`. Output is pixel-only.
Polygon-summary output has been removed.

## Start here: use a terminal

**Jupyter is optional.** Installation, catalog searches, downloads, SNAP
processing, status checks, and aggregation all run from a terminal. You can
inspect the final Parquet table in a notebook later.

The steps below use Windows PowerShell and the explicit prefix
`conda run --no-capture-output -n sarpanel`. This selects the project's Python
even when `conda activate` has not changed the Python used by your shell.
Run commands one at a time, and stop if a command fails.

The workflow is:

1. Clone this repository and create its conda environment once.
2. Install sarpanel into that environment and verify the Python path.
3. Install ESA SNAP separately and verify `gpt -h`.
4. Configure Earthdata credentials in the terminal.
5. Test the supplied demo, or copy and edit a YAML configuration for your own area.
6. Run `plan`, review the download size, then run, check status, and aggregate.

### What gets installed where?

| Component | Purpose | Installation |
|---|---|---|
| ASF Search (`asf_search`) | Search the satellite catalog and authenticate downloads | Already included in `environment.yml` |
| pyroSAR (`pyroSAR` import, `pyrosar` conda package) | Build and execute SNAP processing graphs | Already included in `environment.yml` |
| GDAL and the geospatial Python stack | Read, project, and sample spatial data | Installed through the conda dependencies |
| sarpanel | Coordinate the workflow and write the tables | Install from this clone with pip, after creating the environment |
| ESA SNAP and its Microwave Toolbox | Perform the radar processing through `gpt` | Separate application installer |
| NASA Earthdata account | Authorize satellite-data downloads | Free account and token; not a software package |

**Do not install ASF Search or pyroSAR again after the environment is created.**
The dependency imports are checked below.

## 1. Clone the repository and create the environment

Install [Git](https://git-scm.com/) and
[Miniforge](https://github.com/conda-forge/miniforge) or Anaconda if needed.
Open PowerShell or an Anaconda Prompt where `conda --version` works.

Choose a parent directory for the project, then run:

```powershell
git clone https://github.com/ryjayne89/sarpanel.git
cd sarpanel
conda env create -f environment.yml --solver libmamba -v
```

You must be inside the cloned repository: it contains both `environment.yml`
and `pyproject.toml`. A directory containing only notebooks or input data is
not the repository.

Environment creation is a one-time step. It resolves compatible package
versions and downloads the compiled geospatial stack; the first setup can take
considerable time. `libmamba` is conda's dependency solver, not a sarpanel
runtime dependency. `-v` shows more progress information. See the
[conda environment creation reference](https://docs.conda.io/projects/conda/en/stable/commands/env/create.html).
Wait for the transaction to finish and the terminal prompt to return before
continuing. If prompted to proceed, enter `y`. Do not start another install
from a notebook or another terminal while this one is running.

This guide uses conda for the dependencies and pip for sarpanel itself.
A pip-only virtual environment can work when compatible native dependencies
are already available, but creating a venv alone does not supply GDAL's
native libraries and `osgeo` bindings. See
[pyroSAR's installation guide](https://pyrosar.readthedocs.io/en/latest/general/installation.html).

## 2. Install sarpanel into the correct Python

From the cloned repository:

```powershell
conda run --no-capture-output -n sarpanel python -c "import sys; print(sys.executable)"
conda run --no-capture-output -n sarpanel python -m pip install --no-deps -e .
conda run --no-capture-output -n sarpanel python -c "import sys, sarpanel; print(sys.executable); print(sarpanel.__version__); print(sarpanel.__file__)"
conda run --no-capture-output -n sarpanel python -c "import asf_search, pyroSAR; from osgeo import gdal; print('ASF Search, pyroSAR, and GDAL imports succeeded')"
```

The executable path must contain `envs\sarpanel` (for example,
`D:\Softwares\Anaconda\envs\sarpanel\python.exe`). The environment requests
Python 3.11. A path such as `D:\Softwares\Anaconda\python.exe` is base Anaconda.
Fix the environment selection before continuing.

`--no-deps` reuses the dependencies conda installed. `-e .` installs this clone
in editable mode so code changes are visible without reinstalling the package.
Using `python -m pip` with `conda run` ensures the install goes into the same
Python used by later commands.

You can use `conda activate sarpanel` and shorter commands if activation works
in your shell. Check `python -c "import sys; print(sys.executable)"` first.
The examples below keep the explicit environment selection throughout.

## 3. Install ESA SNAP separately

Creating the conda environment installs pyroSAR, **not SNAP**. pyroSAR builds
processing graphs; SNAP's `gpt` executable actually processes the scenes.

1. Open the [official SNAP download page](https://step.esa.int/main/download/snap-download/).
2. Download **Sentinel Toolboxes** for your operating system. On Windows, choose
   **Windows 64-Bit**.
3. Run the installer and keep the **Microwave Toolbox** selected.
4. Note the installation directory. It must contain `bin\gpt.exe` on Windows
   or `bin/gpt` on Linux.

In PowerShell, replace the example path if SNAP is installed elsewhere:

```powershell
$env:SNAP_HOME = "C:\Program Files\esa-snap"
$env:PATH = "$env:SNAP_HOME\bin;" + $env:PATH
Test-Path "$env:SNAP_HOME\bin\gpt.exe"
gpt -h
```

`Test-Path` should print `True`, and `gpt -h` should display SNAP's help.

In bash on Linux:

```bash
export SNAP_HOME="$HOME/snap"
export PATH="$SNAP_HOME/bin:$PATH"
gpt -h
```

These settings apply to the current terminal and its child processes. Set them
again in each new terminal unless you have configured them persistently.
Keep using this terminal for the remaining steps.

**Known Windows diagnostic issue:** the current `doctor` command checks
`bin/gpt` and can report it missing even when `bin\gpt.exe` exists. If the
PowerShell checks above succeed, that message alone does not mean SNAP is
missing.

## 4. Set up Earthdata login and check the stack

Searching the ASF catalog is public. Downloading scenes requires a free
[NASA Earthdata account](https://urs.earthdata.nasa.gov/).

1. Register or sign in and generate a token from your profile.
2. Authorize **Alaska Satellite Facility Data Access**. If an error says
   `Pre authorization required`, follow its `resolution_url` or the
   Earthdata account's application-authorization instructions.
3. Set the token in the same terminal where `gpt -h` worked.

PowerShell:

```powershell
$env:EARTHDATA_TOKEN = "paste-your-token-here"
```

bash:

```bash
export EARTHDATA_TOKEN="paste-your-token-here"
```

Keep the token private. The package reads it from the environment, not from a
command-line flag. It does not automatically load `.env` files. A new terminal
needs the token set again.

Now run:

```powershell
conda run --no-capture-output -n sarpanel python -m sarpanel.cli doctor
```

Check that Python is from the project environment, credentials say
`VERIFIED against urs.earthdata.nasa.gov`, and pyroSAR reports
`export_extra: True` and `terrain_flattening: True` for the default configuration.
SNAP should be accessible as verified in step 3. `subswaths: False` does not
block the supplied GRD demo, which does not request individual subswaths.

On a shared Linux cluster, credentials can instead be stored in a private
`~/.earthdata_token` file or an Earthdata entry in `~/.netrc`, with mode 600.
On Windows, use the environment token; Unix permission checks on credential
files do not reliably map to Windows permissions.

## 5. Choose and edit your YAML configuration

**The YAML file tells sarpanel where, when, and how to process data.** Installing
the software does not select your own study area or dates.

For the first installation check, you can leave `configs/local_demo.yaml`
unchanged. It uses a small box in central Michigan and June 2020 GRD data, with
no vector file required. For your own study, copy it first:

```powershell
Copy-Item configs/local_demo.yaml configs/my_run.yaml
```

On bash, use `cp configs/local_demo.yaml configs/my_run.yaml`. Open
`configs/my_run.yaml` in a text editor, then change the following values.
This example still contains the Michigan coordinates; replace them with yours:

```yaml
name: my_run

aoi:
  # Longitude first, then latitude: west,south,east,north.
  source: "-84.60,42.68,-84.55,42.72"

search:
  start_date: "2020-06-01"
  end_date: "2020-06-30"
  beam_mode: IW
  polarization: "VV+VH"

processing:
  product_type: GRD
  target_crs: "EPSG:32616"  # UTM 16N for this example; choose your area's CRS.
  target_spacing_m: 10.0
  polarizations: ["VV", "VH"]
  terrain_flattening: true
  speckle_filter: null
  subset_to_aoi: true
  subset_buffer_m: 5000

output:
  root: "./runs/my_run"

n_shards: 1
```

| Setting | What to change |
|---|---|
| `name` | Choose a short run name, such as `my_sites_2020`. |
| `aoi.source` | Enter your bounding box in longitude/latitude, or the path to your polygon file. |
| `search.start_date`, `search.end_date` | Set the study period using quoted `YYYY-MM-DD` dates. Start with a short range. |
| `processing.target_crs` | Choose a projected CRS in metres suitable for your area. The example's `EPSG:32616` is UTM zone 16N; it is not a global default. |
| `processing.target_spacing_m` | Output pixel spacing in metres. Start with `10.0` for this GRD workflow. |
| `output.root` | Choose a new result directory for each different area, date range, or processing configuration. |
| `n_shards` | Keep `1` on a laptop and run `--shard 0`. |

For bounding boxes, the order is **minimum longitude, minimum latitude,
maximum longitude, maximum latitude**. West and south come first. Use negative
longitude west of Greenwich and negative latitude south of the equator.
`aoi.source` bounding-box coordinates are always in EPSG:4326, even though
`processing.target_crs` is projected.

### Using your own polygon file

Replace the `aoi` section with, for example:

```yaml
aoi:
  source: "D:/aoi/my_polygons.gpkg"
  id_column: "site_id"  # Replace with an actual column in your file.
```

GeoPackage, GeoJSON, Shapefile, and GeoParquet inputs are supported. A WKT
polygon string can also be used as `source`. File inputs must have a CRS.
Use forward slashes in Windows YAML paths, as above, to avoid backslash escapes
inside double-quoted strings.

`id_column` selects the column identifying your polygons. It is optional: when
omitted, sarpanel looks for a suitable identifier or generates one. Optional
`layer`, `where`, and `min_area_m2` settings select a layer, filter attributes,
and exclude small polygons. For example, `where: "region == 'north'"`
requires a `region` column in the input file.

All relative input and output paths are resolved from the directory where you
run the command, not from the YAML file's directory. Run from the repository
root consistently, or use absolute paths.

**After editing a configuration, run `plan` again.** Workers use the manifest
written by `plan`; editing the dates or coordinates alone does not refresh it.
Use a new `name` and `output.root` for a changed study so old scene outputs are
not mistaken for completed results. Keep the configuration unchanged while a
run is in progress.

The other files under `configs/` include cluster-specific paths. Start with
`local_demo.yaml` rather than copying those paths to your laptop.

## 6. Plan, process, and inspect the results

Run these commands from the repository root, in the terminal holding your SNAP
settings and Earthdata token. For the unchanged demo:

```powershell
conda run --no-capture-output -n sarpanel python -m sarpanel.cli plan configs/local_demo.yaml
```

Read the **scene count and download size** before continuing. One tested query
for this demo returned 4 scenes and about 4 GB. Results and sizes depend on the
configuration and catalog. Planning writes metadata; it does not download the
satellite archives.

When ready, process the planned scenes:

```powershell
conda run --no-capture-output -n sarpanel python -m sarpanel.cli run configs/local_demo.yaml --shard 0 --strict
```

This downloads scenes and runs SNAP. It can take many minutes per scene, and
SNAP intermediates require disk space beyond the reported archive size.
`--strict` returns a nonzero exit code if any scene fails. A `Completed ...`
message refers to one scene; wait for the shard summary and terminal prompt
before assuming the whole run has finished.

The local demo uses AOI subsetting and has processed a scene successfully on a
16 GB Windows laptop. That does not establish that larger areas or full scenes
fit on every laptop. The current `No SLURM job detected` warning is written for
cluster use: it is not itself a failure and does not require a laptop user to
submit an `sbatch` job.

After processing finishes:

```powershell
conda run --no-capture-output -n sarpanel python -m sarpanel.cli status configs/local_demo.yaml
conda run --no-capture-output -n sarpanel python -m sarpanel.cli aggregate configs/local_demo.yaml
```

For your own study, replace **`configs/local_demo.yaml` with
`configs/my_run.yaml` in every command**: plan, run, status, and aggregate.
Do not plan one configuration and run another.

Outputs for the demo land in:

```text
runs/local_demo_wide/
├── manifest/                        scene manifest, resolved AOI, saved config
├── work/                            downloads and SNAP intermediates
├── observations/                    one Parquet file per processed scene
├── status/                          per-shard scene outcomes
└── local_demo_observations.parquet   combined table, written by aggregate
```

Status files are written after a shard finishes, so the first run may report
no status files while it is still processing. Follow the terminal log for live
progress. Rerunning the same `run` command skips existing scene outputs and
retries unfinished scenes. A scene may legitimately produce `no_data` if too
few valid pixels overlap the polygon.

To inspect the combined table directly in the terminal:

```powershell
conda run --no-capture-output -n sarpanel python -c "import pandas as pd; df = pd.read_parquet('runs/local_demo_wide/local_demo_observations.parquet'); print(df.head()); print(df.columns.tolist())"
```

Acquisition dates and scene IDs appear in measurement column names; the scene
manifest uses `acquisition_date`. The example path above is for the demo. Your combined table is written to
`<output.root>/<name>_observations.parquet`.

For the Michigan cropland smoke test, populated incidence angles around 29–46
degrees, finite pixel backscatter, and valid spatial coordinates are useful
initial checks. A scene covering a polygon should yield many pixel rows. Expected
backscatter depends on the land cover, season, and processing; an error-free run is not a substitute for
checking the measurements.

## Optional: use Jupyter to analyze the output

Jupyter is not needed for the processing workflow above. If you want notebooks
for analysis, register the project environment and launch JupyterLab from the
repository root:

```powershell
conda run --no-capture-output -n sarpanel python -m ipykernel install --user --name sarpanel --display-name "Python (sarpanel)"
conda run --no-capture-output -n sarpanel jupyter lab
```

Select **Python (sarpanel)** in the notebook's kernel menu. Confirm the
interpreter before importing project packages:

```python
import sys
import sarpanel

print(sys.executable)  # Must point to the sarpanel environment.
print(sarpanel.__file__)
```

Run dependency installation in the terminal. An `import sarpanel` that appears
to succeed in base Python can be misleading: from the repository's parent
folder, Python may find the outer `sarpanel` directory as a namespace package.
A `None` module file and a missing `__version__` are signs of that situation.

For analysis, read the output with pandas:

```python
import pandas as pd

df = pd.read_parquet("runs/local_demo_wide/local_demo_observations.parquet")
print(df.head())
print(df.filter(regex=r"^VV_db__").columns.tolist())
```

Use an absolute path if your notebook is saved outside the repository root.

## Command reference and cluster use

All commands below can be invoked with the explicit prefix used above:
`conda run --no-capture-output -n sarpanel python -m sarpanel.cli`.

| Command | What it does |
|---|---|
| `doctor` | Checks package versions, Earthdata credentials, pyroSAR features, and SNAP |
| `plan CONFIG` | Searches ASF and writes a manifest without downloading scene archives |
| `run CONFIG --shard 0 --strict` | Downloads and processes one shard; reports failures through its exit code |
| `status CONFIG` | Summarizes saved per-shard outcomes |
| `aggregate CONFIG` | Outer-joins acquisitions into one row per pixel with dated columns |

Once `conda activate sarpanel` is verified to select the correct Python,
`sarpanel ...` is the shorter equivalent. `run --help` lists the flags.

On SLURM, the scripts use `SARPANEL_ENV`, `SNAP_HOME`, and `SARPANEL_SCRATCH`:

```bash
mkdir -p logs
export SARPANEL_ENV=sarpanel
export SNAP_HOME=/path/to/snap
sbatch slurm/plan.sb configs/your_run.yaml
# Wait for planning to finish successfully before submitting workers.
sbatch --array=0-0 slurm/run_shard.sb configs/your_run.yaml
```

Set the array range to `0` through `n_shards - 1`. Local laptop runs do not use
these SLURM commands.

## Output layout: one pixel row, dates across columns

A combined table has one row per pixel within an AOI. `aoi_id`, `x`, and `y`
identify that pixel. SNAP is instructed to use the same projected grid, pixel
spacing, and zero grid origin across scenes so observations can be joined by
location, using [SNAP standard-grid alignment](https://github.com/senbox-org/s1tbx/blob/master/s1tbx-commons/src/main/java/org/esa/s1tbx/commons/CRSGeoCodingHandler.java). The runner checks the output grid and CRS before extraction.

Every measurement and acquisition-metadata column uses this naming pattern:

```text
<measurement>__<YYYY-MM-DD>__<scene_id>
```

For example, using shortened scene IDs only to make this illustration readable:

| aoi_id | x | y | VV_db__2020-06-08__scene_A | VV_db__2020-06-20__scene_B |
|---|---|---|---|---|
| site_1 | 500005 | 4500005 | -10.2 | -9.8 |
| site_1 | 500015 | 4500005 | -11.4 | missing |

Actual columns use the full scene ID. The scene ID is necessary because two
granules can cover the same pixel on the same date. Both measurements are
retained in separate columns, with no averaging and no silent overwriting.

| Measurement before the date/scene suffix | Meaning |
|---|---|
| `VV_linear`, `VH_linear` | Pixel backscatter in linear power for the requested polarizations |
| `VV_db`, `VH_db` | The same pixel values in decibels |
| `inc_angle` | Projected local incidence angle at the pixel |
| `layover_shadow` | Exported mask value; flagged pixels are excluded by default |
| `date`, `start_time`, `scene_id` | Acquisition timing and granule identity |
| `relative_orbit`, `orbit_direction`, `absolute_orbit` | Track and viewing geometry for that acquisition |
| `platform`, `product_type`, `frame_number` | Acquisition metadata |
| `looks_nominal`, `looks_range`, `looks_azimuth` | Nominal-look estimates for that acquisition |

`x` and `y` are pixel-center coordinates in `processing.target_crs`, in metres.
Overlapping AOIs can contain the same physical pixel under different `aoi_id`
values. The spatial identity is therefore `aoi_id`, `x`, and `y`.

Only valid pixels are extracted from each acquisition. The combined table uses
an **outer join**: it includes pixels observed on any acquisition, and leaves
missing values for dates on which a pixel had no valid measurement.
`aggregate` does not compute spatial means, interpolate missing observations,
or collapse same-date granules. Polygon means, empirical ENL, and uncertainty
of polygon averages are not produced by this package.

The aggregate command currently joins the study in memory. A wide study can
have many rows and many dated columns; for larger studies, retain per-scene
Parquet files and process spatial subsets rather than loading everything at once.

### Migrating earlier output

Earlier versions supported polygon summaries and long-format pixel files with
one row per pixel per acquisition. Both old layouts are rejected by the new
runner and aggregation checks. Legacy `output.granularity: pixel` is accepted
when reading an old YAML, but it is no longer a selectable output mode.
`output.granularity: polygon` raises an error.

The demo now writes to **`runs/local_demo_wide/`**, preserving older
`runs/local_demo/` and `runs/local_demo_pixel/` results. After updating, rerun
`plan`, then `run`, using `configs/local_demo.yaml`; finally run `aggregate`
to join dates across columns. This may redownload scenes and run SNAP again,
including grid alignment. Polygon summaries cannot be expanded back into
pixel data. Use a new `output.root` when migrating your own study, and do not
mix old and new scene files.

## Looks, speckle, and precision

Backscatter from a polygon is an estimate, and its precision depends on how
many independent looks went into it. `sarpanel` records the pieces needed to
quantify that and leaves the choice of whether to use them to you. Nothing in
the default pipeline applies a speckle model on your behalf.

### What is always recorded

Every pixel row carries the processing parameters that bound the look count.

| Column | Meaning |
| --- | --- |
| `looks_nominal` | Looks implied by the configuration: output pixel area over native resolution cell area, floored at one per axis |
| `looks_range`, `looks_azimuth` | The per-axis contributions to that product |

These are geometry rather than measurement. They state how many native
resolution cells fit inside an output pixel, which is an upper bound on
independence. The measured value is almost always lower, because spectral
overlap between looks, range oversampling, and interpolation during terrain
correction all introduce correlation that a ratio of areas cannot see.

Resolution is used rather than sample spacing, deliberately. Sentinel-1 range
samples are spaced 2.3 m apart inside a 2.7 m resolution cell, so counting
samples would credit a 10 m pixel with 4.35 range looks when it can hold at
most 3.70 independent ones.

### Optional measurement during processing

Set `output.measure_scene_enl: true` and each scene is measured once while its
raster is open, adding three columns.

| Column | Meaning |
| --- | --- |
| `scene_enl` | Equivalent number of looks, estimated from the scene's most homogeneous regions |
| `scene_enl_homogeneous_fraction` | Share of sampled blocks consistent with that ENL; low values mean the estimate is a lower bound |
| `correlation_inflation` | Variance inflation factor of the spatial autocorrelation, written as `kappa` below |

This is off by default because the pixel output is complete without it and the
same estimators are available as library functions. The one thing it offers
that cannot be reproduced afterwards is `correlation_inflation`, because
measuring inter-pixel correlation needs contiguous raster, and the written table
retains only polygon interiors.

ENL is measured per scene rather than per polygon on purpose. It is a property
of the sensor and the processing chain, so a per-polygon estimate would absorb
that polygon's own spatial structure and report texture under the name of a
look count.

### Applying it yourself

The library exposes each piece, so you can aggregate on whatever basis suits the
analysis: whole polygons, sub-polygon zones, pixel neighbourhoods, or temporal
windows.

```python
from sarpanel import radiometry as rad

# One scene, measured from a 2-D linear-power array
scene = rad.estimate_scene_enl(vv_array)        # {'enl': ..., 'homogeneous_fraction': ...}
kappa = rad.autocorrelation_inflation_factor(vv_array)

# One polygon on one date, from its pixel values
summary = rad.aggregate_polygon(
    pixels_linear,
    enl=scene["enl"],
    inflation_factor=kappa,
)
# -> mean_linear, mean_db, n_pixels, n_effective, cv, texture_cv, se_db
```

Or assemble it from the parts:

```python
cv       = rad.coefficient_of_variation(pixels_linear)
texture  = rad.texture_cv(cv, enl)                       # speckle removed
n_eff    = rad.effective_sample_size(len(pixels_linear), kappa)
se_db    = rad.speckle_standard_error_db(n_eff, enl)
```

Two points worth knowing before using these.

**Aggregate in linear power, not decibels.** Averaging `VV_db` is biased low,
and the bias grows with within-polygon heterogeneity, so it is correlated with
whatever spatial signal you are trying to measure. Both columns are written;
only one is correct for a mean. `aggregate_polygon` and `mean_linear_then_db`
handle this.

**Pass an independent sample count to the standard error, not a pixel count.**
Interpolation during terrain correction raises per-pixel ENL by averaging
neighbours, but it creates no information, because those neighbours are now
correlated. Using the raw pixel count credits the variance reduction while
ignoring the correlation that produced it, which understates the error by the
square root of `kappa`. After bilinear resampling onto a finer grid that is a
factor of about two. `effective_sample_size` converts.

### Separating texture from speckle

An observed coefficient of variation mixes speckle with real spatial structure.
Given the scene ENL they separate under the standard multiplicative model, and
`texture_cv` performs the inversion. The result is comparable across polygons of
different sizes and across scenes processed with different look counts, which
the raw CV is not, since fewer looks inflate every polygon's CV at once.

### References

Oliver, C. and Quegan, S. (2004). *Understanding Synthetic Aperture Radar
Images*. SciTech Publishing.

Anfinsen, S. N., Doulgeris, A. P. and Eltoft, T. (2009). Estimation of the
equivalent number of looks in polarimetric synthetic aperture radar imagery.
*IEEE Transactions on Geoscience and Remote Sensing*, 47(11), 3795-3809.

## Choices that change the result

**The incidence angle is a terrain-correction output band.** It is requested
through pyroSAR's `export_extra`, so SNAP writes it on the same grid as the
backscatter. Reading the tie-point incidence grid out of the SNAP intermediate
directory is a different grid (pre-terrain-correction). Those numbers can look
plausible and still be in the wrong place. `sarpanel doctor` warns when the
installed pyroSAR cannot write the band.

**The catalog is queried once.** `plan` writes a manifest. Workers read their
assigned rows. Searching again inside every worker can skip or repeat scenes,
because result order is not stable.

**Filenames include the scene id.** Two granules can share a date and an orbit
direction. Naming files only by date would drop one of them.

**Backscatter is processed in linear power.** Pixel output retains both
linear power and its decibel conversion in dated columns. For any downstream summaries, average
linear power first, then convert that mean to decibels. Averaging decibels
biases the result low. The package refuses `scaling: dB` at geocode time.

**A finished scene is detected before it is downloaded again.** Rerunning
`run` continues unfinished scenes after validating the pixel-only schema.

**SLC and GRD are both supported.** `product_type: GRD` is the right first
test: smaller downloads, faster processing. `product_type: SLC` keeps phase
and gives explicit control of multilooking, at roughly five times the download
volume. For SLC, set `search.relative_orbits` to the tracks you actually want.
Different tracks have different viewing geometry.

**Speckle filtering is off by default.** A speckle filter changes the
equivalent-number-of-looks estimate that the standard errors come from.

## Tests

The tests cover radiometry, extraction, AOI parsing, CLI behavior, the manifest,
and provenance. They do not require SNAP or Earthdata credentials.

```powershell
conda run --no-capture-output -n sarpanel python -m pytest -q
```

The current suite includes Unix token-file permission tests that can fail on
Windows because `chmod(600)` does not establish Unix mode-600 permissions there.
Use an environment token on Windows; those test failures do not establish that
token authentication is broken.

## Troubleshooting

| What you see | What to check |
|---|---|
| `environment.yml` not found, or pip says no `pyproject.toml` | Change into the cloned repository, not the folder containing only your notebooks or input data. |
| A long `Solving environment` step | Use `--solver libmamba -v` to see progress. The first solve can take time. If it stops progressing, save the output before interrupting; avoid concurrent installs. |
| An unknown `libmamba` solver | Check your conda installation against the current conda documentation; the environment-creation command requires that solver to be available. |
| Versions or warnings coming from base Anaconda | Use `conda run -n sarpanel python ...` for both installation and execution; verify `sys.executable` before installing more packages. |
| `ModuleNotFoundError: sarpanel` inside the new environment | Run the step-2 editable install with the explicit environment prefix. Installing in base does not install it in `sarpanel`. |
| Missing `__version__`, with `sarpanel.__file__` equal to `None` | Python is finding the outer repository folder instead of the installed package. Use the project environment and its editable installation. |
| `No module named 'osgeo'`, `geopandas`, or `rasterio` | Verify the interpreter first. If the project environment really lacks the dependencies, check the environment-creation output. |
| `pg_config executable not found` during a pip install | A dependency is attempting a source build. Use the conda dependency stack in this guide, then install sarpanel with `--no-deps`. |
| `gpt MISSING` on Windows | Check `Test-Path "$env:SNAP_HOME\bin\gpt.exe"` and `gpt -h`. The current doctor check can incorrectly look for extensionless `gpt`. |
| `gpt -h` fails | Check that SNAP and its Microwave Toolbox are installed, SNAP_HOME is the installation folder, and its bin directory is on PATH in this terminal. |
| `No SLURM job detected` during a local run | This cluster-oriented warning is not itself a processing error. Watch the following SNAP logs and the final scene status. |
| `Invalid/Expired token` or rejected credentials | Set a current token in the same terminal, without surrounding whitespace. |
| `Pre authorization required` | Authorize the ASF data-access application using the instructions or resolution URL in the error. |
| Credential-file `PermissionError` on Windows | Use `EARTHDATA_TOKEN` in the environment rather than a Unix-permission credential file. |
| Login connection resets | Check network, firewall, VPN, or proxy access to `urs.earthdata.nasa.gov`. |
| `plan` finds no scenes | Check longitude/latitude order, input CRS, dates, orbit filters, and coverage. Try a wider date range and replan. |
| `export_extra: False` | The installed pyroSAR cannot export the required incidence band. Check or upgrade it in the project environment. |
| No saved status while processing | Status is saved at shard completion. Use the running terminal log to follow the first run. |
| SNAP is killed or runs out of memory | Reduce the study area, retain AOI subsetting, or set `snap_tile_cache_gb` and `snap_threads` for your machine. Larger jobs may need more RAM. |
| `no_data` scenes | Some edge overlaps have too few valid pixels. Review the footprint and `min_valid_pixels` setting. |

## Authors

`sarpanel` is written and maintained by **Ryan Jayne**
([ORCID 0009-0001-4662-9686](https://orcid.org/0009-0001-4662-9686)), with
contributions from **Dev Jyoti Ghosh Arnab**.

It grew out of SAR-based agricultural and land-use research, but the design is
deliberately domain-neutral: the package knows about radar geometry and
radiometry, and nothing about what the polygons handed to it represent. Issues
and pull requests are welcome, including from fields it was not originally
built for.

### Citation

If you use `sarpanel`, please cite it. `CITATION.cff` in the repository root
carries the machine-readable version, which GitHub renders as a "Cite this
repository" link.

## License

[MIT](LICENSE)
