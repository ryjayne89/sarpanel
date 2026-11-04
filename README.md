# sarpanel

Sentinel-1 pixel time series, written to Parquet, with spatial coordinates,
backscatter, and acquisition geometry kept intact.

Provide an area of interest, either one polygon or multipolygon, plus a date range.
It searches the Alaska Satellite Facility (ASF) archive, processes the matching
Sentinel-1 scenes with [ESA SNAP](https://step.esa.int/main/toolboxes/snap/)
through [pyroSAR](https://github.com/johntruckenbrodt/pyroSAR), and writes one
row per pixel within the AOI with dated columns for each acquisition. Each scene gets
its own file, and `aggregate` joins the acquisitions side by side on the pixel
coordinates. 

The polygons can be anything with a boundary. Nothing downstream looks at what
a polygon is supposed to represent, so a run over one polygon and a run over
300,000 differ only in how many rows come out. You can pass any vector file
GeoPandas can read, a WKT string, a bounding box written as
`minx,miny,maxx,maxy`, or a list of bounding boxes.

## What this is for

Sentinel-1 has imaged nearly the whole land surface every 6 to 12 days since 2014,
through cloud and at night, and the archive is free. Getting the data has never
been the hard part. The hard part is turning a decade of radar scenes into a
table you can model. That takes a working SNAP install, a catalog search that
doesn't quietly drop scenes, careful handling of terrain and radiometry, and
some way to keep track of which pixel came from where and under what viewing
angle. sarpanel handles that middle part.

Since it makes no assumptions about what the polygons are, the same pipeline
covers problems that usually need separate tools.

| Domain | What the polygons are | What you can get from the time series |
| --- | --- | --- |
| Agriculture | Parcels, management zones, trial plots | Crop type, phenology, biomass accumulation, harvest and tillage dates, within-parcel variability |
| Land use and land cover change | Administrative units, concession boundaries, sample frames | Forest loss and regrowth, conversion to cropland, wetland drainage, encroachment |
| Infrastructure | Building footprints, road and rail corridors, dams, pipelines, port facilities | Construction progress, subsidence-prone surfaces, activity at facilities, change after an event |
| Water and ice | Reservoirs, floodplains, river reaches, glacier termini, sea ice zones | Inundation extent and timing, reservoir drawdown, melt onset, terminus retreat |
| Hazards and response | Burn perimeters, landslide scars, earthquake-affected districts | Damage proxy mapping, pre-event baselines, recovery over time |
| Mining and extraction | Pit boundaries, tailings facilities, well pads | Footprint expansion, surface disturbance, activity monitoring |
| Calibration and method work | Corner reflector sites, homogeneous reference areas | Radiometric stability, look-count checks, speckle characterization |

Radar works for all of these for the same reason. It measures surface geometry,
roughness, and moisture instead of reflected sunlight, so it sees through cloud,
works at night, and responds to physical structure. The tradeoff is that it
needs more careful handling than optical imagery, and handling it carefully is
most of what this package does.

### What it does that a raster pipeline doesn't

Several good tools already turn Sentinel-1 into calibrated rasters, including
HyP3, pyroSAR itself, and the OPERA RTC products. They stop at imagery and leave
you to sample it, and sampling is usually where the acquisition metadata gets
thrown away.

sarpanel stops at a table instead, and each row keeps the conditions it was
measured under.

- Per-pixel coordinates on a fixed, snapped grid, so a pixel is the same pixel
  across years. The time series is a real panel rather than a stack of
  near-misses.
- Projected local incidence angle for every acquisition, read from the
  terrain-correction output band rather than interpolated from tie points.
  Backscatter depends on viewing angle, and two orbit tracks can differ by 9
  degrees or more, so ignoring it means fitting geometry as though it were
  signal.
- Orbit track, platform, and timing, so you can condition on viewing geometry
  instead of pooling everything together.
- Layover and shadow masks, which flag pixels that aren't really measurements.
- What you need to work out precision, described under
  [Looks, speckle, and precision](#looks-speckle-and-precision). Polygon size
  and processing history both affect how reliable an estimate is, and the
  package records enough to weight observations accordingly instead of treating
  them all as equally good.
- Provenance in every file, including software versions, the DEM used, and the
  full run configuration, so a result is still traceable after the file moves
  somewhere else.

### Scale it has been run at

The pipeline has processed a full Sentinel-1 SLC archive over an area 125 by 180
km. That was 692 scenes from 2016 to 2023 across two orbit tracks, 327 polygons,
347 separate acquisition dates, and roughly 259 million pixel observations, with
691 of the 692 scenes finishing. It runs on a laptop for a single polygon and on
a SLURM array for a whole archive, from the same configuration file.

This tool used to be called `sar2parquet`. The repository, the Python import,
and the command are all `sarpanel` now. Output is pixel-only, and the old
polygon-summary output has been removed.

## Start here, in a terminal

Jupyter is optional. Installing, searching the catalog, downloading, running
SNAP, checking status, and aggregating all happen in a terminal. You can open
the final Parquet table in a notebook afterwards if you want.

The steps below use Windows PowerShell with the prefix
`conda run --no-capture-output -n sarpanel`. That picks the project's Python even
when `conda activate` hasn't changed which Python your shell uses. Run the
commands one at a time and stop if one fails.

Here's the shape of it.

1. Clone this repository and create its conda environment, once.
2. Install sarpanel into that environment and check the Python path.
3. Install ESA SNAP separately and check that `gpt -h` works.
4. Set up Earthdata credentials in the terminal.
5. Try the demo, or copy a YAML config and edit it for your own area.
6. Run `plan`, look at the download size, then run, check status, and aggregate.

### What gets installed where

| Component | What it's for | How it gets installed |
|---|---|---|
| ASF Search (`asf_search`) | Searches the satellite catalog and authenticates downloads | Already in `environment.yml` |
| pyroSAR (`pyroSAR` import, `pyrosar` conda package) | Builds and runs SNAP processing graphs | Already in `environment.yml` |
| GDAL and the geospatial Python stack | Reads, projects, and samples spatial data | Comes with the conda dependencies |
| sarpanel | Runs the workflow and writes the tables | Install from this clone with pip, after the environment exists |
| ESA SNAP and its Microwave Toolbox | Does the actual radar processing through `gpt` | Separate application installer |
| NASA Earthdata account | Authorizes the downloads | Free account and token, not a software package |

Don't install ASF Search or pyroSAR again once the environment exists. There's a
check for the imports below.

## 1. Clone the repository and create the environment

Install [Git](https://git-scm.com/) and
[Miniforge](https://github.com/conda-forge/miniforge) or Anaconda if you don't
have them. Open PowerShell or an Anaconda Prompt where `conda --version` works.

Pick a parent directory for the project, then run:

```powershell
git clone https://github.com/ryjayne89/sarpanel.git
cd sarpanel
conda env create -f environment.yml --solver libmamba -v
```

You need to be inside the cloned repository, which has both `environment.yml`
and `pyproject.toml` in it. A folder holding only notebooks or input data is not
the repository.

Creating the environment is a one-time step. It works out compatible package
versions and downloads the compiled geospatial stack, so the first setup can
take a while. `libmamba` is conda's dependency solver, not something sarpanel
needs at runtime, and `-v` just shows more progress. The
[conda environment creation reference](https://docs.conda.io/projects/conda/en/stable/commands/env/create.html)
has the details. Wait for the transaction to finish and the prompt to come back
before moving on. Enter `y` if it asks you to proceed. Don't start another
install from a notebook or a second terminal while this one is running.

This guide uses conda for the dependencies and pip for sarpanel itself. A
pip-only virtual environment can work if compatible native dependencies are
already around, but creating a venv on its own won't give you GDAL's native
libraries or the `osgeo` bindings. See
[pyroSAR's installation guide](https://pyrosar.readthedocs.io/en/latest/general/installation.html).

## 2. Install sarpanel into the right Python

From the cloned repository:

```powershell
conda run --no-capture-output -n sarpanel python -c "import sys; print(sys.executable)"
conda run --no-capture-output -n sarpanel python -m pip install --no-deps -e .
conda run --no-capture-output -n sarpanel python -c "import sys, sarpanel; print(sys.executable); print(sarpanel.__version__); print(sarpanel.__file__)"
conda run --no-capture-output -n sarpanel python -c "import asf_search, pyroSAR; from osgeo import gdal; print('ASF Search, pyroSAR, and GDAL imports succeeded')"
```

The executable path has to contain `envs\sarpanel`, something like
`D:\Softwares\Anaconda\envs\sarpanel\python.exe`. The environment asks for
Python 3.11. If you see a path like `D:\Softwares\Anaconda\python.exe` that's
base Anaconda, so fix the environment selection before going further.

`--no-deps` reuses what conda installed. `-e .` installs this clone in editable
mode, so code changes show up without reinstalling. Using `python -m pip` with
`conda run` makes sure the install lands in the same Python the later commands
use.

You can use `conda activate sarpanel` and shorter commands if activation works
in your shell. Check `python -c "import sys; print(sys.executable)"` first. The
examples below keep the explicit environment prefix throughout.

## 3. Install ESA SNAP separately

Creating the conda environment installs pyroSAR, not SNAP. pyroSAR builds the
processing graphs, and SNAP's `gpt` executable is what actually processes the
scenes.

1. Open the [SNAP download page](https://step.esa.int/main/download/snap-download/).
2. Download Sentinel Toolboxes for your operating system. On Windows pick
   Windows 64-Bit.
3. Run the installer and leave the Microwave Toolbox selected.
4. Note where it installed. The folder needs to contain `bin\gpt.exe` on Windows
   or `bin/gpt` on Linux.

In PowerShell, swapping the path if SNAP is somewhere else:

```powershell
$env:SNAP_HOME = "C:\Program Files\esa-snap"
$env:PATH = "$env:SNAP_HOME\bin;" + $env:PATH
Test-Path "$env:SNAP_HOME\bin\gpt.exe"
gpt -h
```

`Test-Path` should print `True`, and `gpt -h` should print SNAP's help.

In bash on Linux:

```bash
export SNAP_HOME="$HOME/snap"
export PATH="$SNAP_HOME/bin:$PATH"
gpt -h
```

These settings only apply to the current terminal and anything it starts. Set
them again in each new terminal unless you've made them permanent. Stay in this
terminal for the rest of the steps.

One known Windows quirk. The `doctor` command looks for `bin/gpt` and can report
it missing even when `bin\gpt.exe` is right there. If the PowerShell checks above
worked, that message on its own doesn't mean SNAP is missing.

## 4. Set up Earthdata login and check the stack

Searching the ASF catalog is public. Downloading scenes needs a free
[NASA Earthdata account](https://urs.earthdata.nasa.gov/).

1. Register or sign in, then generate a token from your profile.
2. Authorize Alaska Satellite Facility Data Access. If you get an error saying
   `Pre authorization required`, follow the `resolution_url` in it or the
   application-authorization instructions on your Earthdata account.
3. Set the token in the same terminal where `gpt -h` worked.

PowerShell:

```powershell
$env:EARTHDATA_TOKEN = "paste-your-token-here"
```

bash:

```bash
export EARTHDATA_TOKEN="paste-your-token-here"
```

Keep the token private. The package reads it from the environment rather than
from a command-line flag, and it doesn't load `.env` files automatically. A new
terminal needs the token set again.

Now run:

```powershell
conda run --no-capture-output -n sarpanel python -m sarpanel.cli doctor
```

Check that Python comes from the project environment, that credentials say
`VERIFIED against urs.earthdata.nasa.gov`, and that pyroSAR reports
`export_extra: True` and `terrain_flattening: True` for the default
configuration. SNAP should be reachable as you verified in step 3.
`subswaths: False` won't stop the GRD demo, which doesn't ask for individual
subswaths.

On a shared Linux cluster you can keep credentials in a private
`~/.earthdata_token` file or an Earthdata entry in `~/.netrc`, both with mode
600. On Windows, use the environment token instead, since Unix permission checks
on credential files don't map cleanly onto Windows permissions.

## 5. Pick and edit your YAML configuration

The YAML file tells sarpanel where, when, and how to process. Installing the
software doesn't choose your study area or dates for you.

For a first check you can leave `configs/local_demo.yaml` alone. It uses a small
box in central Michigan with June 2020 GRD data and needs no vector file. For
your own work, copy it first:

```powershell
Copy-Item configs/local_demo.yaml configs/my_run.yaml
```

On bash that's `cp configs/local_demo.yaml configs/my_run.yaml`. Open
`configs/my_run.yaml` in an editor and change the values below. This example
still has the Michigan coordinates in it, so replace them with yours.

```yaml
name: my_run

aoi:
  # Longitude first, then latitude: west, south, east, north.
  source: "-84.60,42.68,-84.55,42.72"

search:
  start_date: "2020-06-01"
  end_date: "2020-06-30"
  beam_mode: IW
  polarization: "VV+VH"

processing:
  product_type: GRD
  target_crs: "EPSG:32616"  # UTM 16N for this example. Use your area's CRS.
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
| `name` | A short run name, like `my_sites_2020`. |
| `aoi.source` | Your bounding box in longitude and latitude, or the path to your polygon file. |
| `search.start_date`, `search.end_date` | Your study period, as quoted `YYYY-MM-DD` dates. Start with a short range. |
| `processing.target_crs` | A projected CRS in meters that suits your area. `EPSG:32616` here is UTM zone 16N, not a sensible global default. |
| `processing.target_spacing_m` | Output pixel spacing in meters. `10.0` is a good start for this GRD workflow. |
| `output.root` | A fresh result directory for each different area, date range, or processing setup. |
| `n_shards` | Leave it at `1` on a laptop and run `--shard 0`. |

Bounding boxes go minimum longitude, minimum latitude, maximum longitude,
maximum latitude, so west and south come first. Use negative longitude west of
Greenwich and negative latitude south of the equator. Bounding-box coordinates
in `aoi.source` are always EPSG:4326 even though `processing.target_crs` is
projected.

### Using your own polygon file

Replace the `aoi` section with something like this.

```yaml
aoi:
  source: "D:/aoi/my_polygons.gpkg"
  id_column: "site_id"  # Use a column that actually exists in your file.
```

GeoPackage, GeoJSON, Shapefile, and GeoParquet all work, and a WKT polygon
string works as `source` too. File inputs need a CRS. Use forward slashes in
Windows YAML paths, as above, so you don't have to escape backslashes inside
double quotes.

`id_column` picks the column that identifies your polygons. It's optional, and
if you leave it out sarpanel looks for something suitable or generates
identifiers. There are also optional `layer`, `where`, and `min_area_m2` settings
for choosing a layer, filtering on attributes, and dropping small polygons. For
instance `where: "region == 'north'"` needs a `region` column in the file.

Relative input and output paths resolve from wherever you run the command, not
from the YAML file's own directory. Either run from the repository root
consistently or use absolute paths.

After you edit a configuration, run `plan` again. Workers read the manifest that
`plan` wrote, so changing dates or coordinates on their own doesn't refresh it.
Use a new `name` and `output.root` for a changed study, otherwise old scene
outputs can look like finished results. Leave the configuration alone while a
run is going.

The other files under `configs/` have cluster-specific paths in them. Start from
`local_demo.yaml` rather than copying those onto your laptop.

## 6. Plan, process, and look at the results

Run these from the repository root, in the terminal that has your SNAP settings
and Earthdata token. For the unchanged demo:

```powershell
conda run --no-capture-output -n sarpanel python -m sarpanel.cli plan configs/local_demo.yaml
```

Read the scene count and download size before you continue. One test query for
this demo came back with 4 scenes and about 4 GB, though that depends on the
configuration and the catalog. Planning writes metadata and downloads nothing.

When you're ready, process the planned scenes.

```powershell
conda run --no-capture-output -n sarpanel python -m sarpanel.cli run configs/local_demo.yaml --shard 0 --strict
```

This downloads scenes and runs SNAP. Expect many minutes per scene, and SNAP's
intermediate files need disk space beyond the archive size it reported.
`--strict` makes it exit nonzero if any scene fails. A `Completed ...` message
is about one scene, so wait for the shard summary and the prompt before assuming
the whole run is done.

The local demo uses AOI subsetting and has processed a scene on a 16 GB Windows
laptop. That doesn't mean larger areas or full scenes will fit on every laptop.
The `No SLURM job detected` warning is written for cluster use. It isn't a
failure and doesn't mean a laptop user needs to submit an `sbatch` job.

Once processing finishes:

```powershell
conda run --no-capture-output -n sarpanel python -m sarpanel.cli status configs/local_demo.yaml
conda run --no-capture-output -n sarpanel python -m sarpanel.cli aggregate configs/local_demo.yaml
```

For your own study, swap `configs/local_demo.yaml` for `configs/my_run.yaml` in
all four commands. Don't plan one configuration and run another.

Demo output lands here.

```text
runs/local_demo_wide/
├── manifest/                        scene manifest, resolved AOI, saved config
├── work/                            downloads and SNAP intermediates
├── observations/                    one Parquet file per processed scene
├── status/                          per-shard scene outcomes
└── local_demo_observations.parquet   combined table, written by aggregate
```

Status files get written after a shard finishes, so the first run may show no
status files while it's still working. Follow the terminal log for live
progress. Running the same `run` command again skips scenes that already
finished and retries the ones that didn't. A scene can legitimately come back
`no_data` if too few valid pixels overlap the polygon.

To look at the combined table from the terminal:

```powershell
conda run --no-capture-output -n sarpanel python -c "import pandas as pd; df = pd.read_parquet('runs/local_demo_wide/local_demo_observations.parquet'); print(df.head()); print(df.columns.tolist())"
```

Acquisition dates and scene IDs show up in the measurement column names, while
the scene manifest uses `acquisition_date`. The path above is the demo's. Your
own combined table goes to `<output.root>/<name>_observations.parquet`.

For the Michigan cropland smoke test, worth checking that incidence angles come
out populated and somewhere around 29 to 46 degrees, that pixel backscatter is
finite, and that the spatial coordinates look right. A scene covering a polygon
should give you a lot of pixel rows. What counts as reasonable backscatter
depends on land cover, season, and processing, and a run finishing without
errors is not the same as the measurements being right.

## Optional, using Jupyter for analysis

You don't need Jupyter for any of the processing above. If you want notebooks
for analysis, register the project environment and start JupyterLab from the
repository root.

```powershell
conda run --no-capture-output -n sarpanel python -m ipykernel install --user --name sarpanel --display-name "Python (sarpanel)"
conda run --no-capture-output -n sarpanel jupyter lab
```

Pick Python (sarpanel) from the notebook's kernel menu, and confirm the
interpreter before importing anything from the project.

```python
import sys
import sarpanel

print(sys.executable)  # Should point at the sarpanel environment.
print(sarpanel.__file__)
```

Install dependencies from the terminal rather than from a notebook. An
`import sarpanel` that seems to work in base Python can mislead you, because
from the repository's parent folder Python may pick up the outer `sarpanel`
directory as a namespace package. A `None` module file and a missing
`__version__` are the giveaways.

For analysis, read the output with pandas.

```python
import pandas as pd

df = pd.read_parquet("runs/local_demo_wide/local_demo_observations.parquet")
print(df.head())
print(df.filter(regex=r"^VV_db__").columns.tolist())
```

Use an absolute path if your notebook lives outside the repository root.

## Command reference and cluster use

All of these take the same prefix used above,
`conda run --no-capture-output -n sarpanel python -m sarpanel.cli`.

| Command | What it does |
|---|---|
| `doctor` | Checks package versions, Earthdata credentials, pyroSAR features, and SNAP |
| `plan CONFIG` | Searches ASF and writes a manifest, without downloading scene archives |
| `run CONFIG --shard 0 --strict` | Downloads and processes one shard, and reports failures through its exit code |
| `status CONFIG` | Summarizes the saved per-shard outcomes |
| `aggregate CONFIG` | Outer-joins acquisitions into one row per pixel with dated columns |

Once you've confirmed `conda activate sarpanel` selects the right Python,
`sarpanel ...` is the shorter equivalent. `run --help` lists the flags.

On SLURM the scripts use `SARPANEL_ENV`, `SNAP_HOME`, and `SARPANEL_SCRATCH`.

```bash
mkdir -p logs
export SARPANEL_ENV=sarpanel
export SNAP_HOME=/path/to/snap
sbatch slurm/plan.sb configs/your_run.yaml
# Wait for planning to finish before submitting workers.
sbatch --array=0-0 slurm/run_shard.sb configs/your_run.yaml
```

Set the array range from `0` to `n_shards - 1`. Laptop runs don't use any of
this.

## Output layout, one pixel row with dates across columns

A combined table has one row per pixel inside an AOI, identified by `aoi_id`,
`x`, and `y`. SNAP is told to use the same projected grid, pixel spacing, and
zero grid origin for every scene, so observations can be joined by location.
That uses [SNAP standard-grid alignment](https://github.com/senbox-org/s1tbx/blob/master/s1tbx-commons/src/main/java/org/esa/s1tbx/commons/CRSGeoCodingHandler.java),
and the runner checks the output grid and CRS before extracting anything.

Every measurement and acquisition-metadata column is named like this.

```text
<measurement>__<YYYY-MM-DD>__<scene_id>
```

Here's an example, with the scene IDs shortened just to keep it readable.

| aoi_id | x | y | VV_db__2020-06-08__scene_A | VV_db__2020-06-20__scene_B |
|---|---|---|---|---|
| site_1 | 500005 | 4500005 | -10.2 | -9.8 |
| site_1 | 500015 | 4500005 | -11.4 | missing |

Real columns carry the full scene ID. The scene ID has to be there because two
granules can cover the same pixel on the same date. Both measurements are kept
in separate columns, with no averaging and nothing silently overwritten.

| Measurement, before the date and scene suffix | What it is |
|---|---|
| `VV_linear`, `VH_linear` | Pixel backscatter in linear power for the polarizations you asked for |
| `VV_db`, `VH_db` | The same values in decibels |
| `inc_angle` | Projected local incidence angle at that pixel |
| `layover_shadow` | Exported mask value. Flagged pixels are dropped by default |
| `date`, `start_time`, `scene_id` | Acquisition timing and granule identity |
| `relative_orbit`, `orbit_direction`, `absolute_orbit` | Track and viewing geometry for that acquisition |
| `platform`, `product_type`, `frame_number` | Acquisition metadata |
| `looks_nominal`, `looks_range`, `looks_azimuth` | Nominal look counts for that acquisition |

`x` and `y` are pixel-center coordinates in `processing.target_crs`, in meters.
Overlapping AOIs can hold the same physical pixel under different `aoi_id`
values, so the spatial identity is the combination of `aoi_id`, `x`, and `y`.

Only valid pixels get extracted from each acquisition. The combined table uses
an outer join, so it includes any pixel observed on any acquisition and leaves
missing values where a pixel had no valid measurement on a given date.
`aggregate` doesn't compute spatial means, fill in missing observations, or
collapse same-date granules.

Aggregation currently joins the study in memory. A wide study can have a lot of
rows and a lot of dated columns, so for bigger studies keep the per-scene
Parquet files and work on spatial subsets instead of loading it all at once.

### Migrating older output

Earlier versions wrote polygon summaries and long-format pixel files with one
row per pixel per acquisition. The new runner and aggregation checks reject both
layouts. A legacy `output.granularity: pixel` is accepted when reading an old
YAML but is no longer something you select, and `output.granularity: polygon`
raises an error.

The demo now writes to `runs/local_demo_wide/`, which leaves any older
`runs/local_demo/` and `runs/local_demo_pixel/` results alone. After updating,
rerun `plan`, then `run`, with `configs/local_demo.yaml`, then `aggregate` to
join the dates across columns. That may redownload scenes and run SNAP again,
including the grid alignment. Polygon summaries can't be turned back into pixel
data. Use a new `output.root` when migrating your own study, and don't mix old
and new scene files.

## Looks, speckle, and precision

Backscatter from a polygon is an estimate, and how precise it is depends on how
many independent looks went into it. sarpanel records what you need to work that
out and leaves it up to you whether to use it. Nothing in the default pipeline
applies a speckle model for you.

### What always gets recorded

Every pixel row carries the processing parameters that bound the look count.

| Column | What it is |
| --- | --- |
| `looks_nominal` | Looks implied by the configuration, meaning output pixel area over native resolution cell area, floored at one per axis |
| `looks_range`, `looks_azimuth` | The per-axis pieces of that product |

These are geometry, not measurement. They tell you how many native resolution
cells fit inside an output pixel, which is an upper bound on independence. The
measured value is nearly always lower, because spectral overlap between looks,
range oversampling, and interpolation during terrain correction all add
correlation that a ratio of areas can't see.

Resolution is used here rather than sample spacing, on purpose. Sentinel-1 range
samples sit 2.3 m apart inside a 2.7 m resolution cell, so counting samples
would credit a 10 m pixel with 4.35 range looks when it can hold 3.70
independent ones at most.

### Optional measurement during processing

Set `output.measure_scene_enl: true` and each scene gets measured once while its
raster is open, which adds three columns.

| Column | What it is |
| --- | --- |
| `scene_enl` | Equivalent number of looks, estimated from the scene's most homogeneous regions |
| `scene_enl_homogeneous_fraction` | Share of sampled blocks consistent with that ENL. A low value means the estimate is a lower bound |
| `correlation_inflation` | Variance inflation factor of the spatial autocorrelation, called `kappa` below |

This is off by default, because the pixel output is complete without it and the
same estimators are there as library functions. The one thing it gives you that
you can't get later is `correlation_inflation`, since measuring inter-pixel
correlation needs contiguous raster and the written table only keeps polygon
interiors.

ENL is measured per scene rather than per polygon deliberately. It's a property
of the sensor and the processing chain, so estimating it per polygon would soak
up that polygon's own spatial structure and report texture under the name of a
look count.

### Applying it yourself

The library exposes each piece, so you can aggregate however suits your
analysis, whether that's whole polygons, sub-polygon zones, pixel
neighborhoods, or time windows.

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

Or build it up from the parts.

```python
cv       = rad.coefficient_of_variation(pixels_linear)
texture  = rad.texture_cv(cv, enl)                       # speckle removed
n_eff    = rad.effective_sample_size(len(pixels_linear), kappa)
se_db    = rad.speckle_standard_error_db(n_eff, enl)
```

Two things worth knowing before you use these.

Aggregate in linear power, not decibels. Averaging `VV_db` is biased low, and
the bias grows with within-polygon heterogeneity, so it ends up correlated with
whatever spatial signal you're trying to measure. Both columns are written, but
only one of them is correct for a mean. `aggregate_polygon` and
`mean_linear_then_db` take care of this.

Pass an independent sample count to the standard error rather than a pixel
count. Interpolation during terrain correction raises per-pixel ENL by averaging
neighbors, but it creates no information, because those neighbors are now
correlated. Using the raw pixel count takes credit for the variance reduction
while ignoring the correlation that caused it, which understates the error by
the square root of `kappa`. After bilinear resampling onto a finer grid that's
roughly a factor of two. `effective_sample_size` does the conversion.

### Separating texture from speckle

An observed coefficient of variation mixes speckle together with real spatial
structure. If you know the scene ENL the two separate under the standard
multiplicative model, and `texture_cv` does that inversion. The result is
comparable across polygons of different sizes and across scenes processed with
different look counts, which the raw CV isn't, since fewer looks inflate every
polygon's CV at once.

### References

Oliver, C. and Quegan, S. (2004). *Understanding Synthetic Aperture Radar
Images*. SciTech Publishing.

Anfinsen, S. N., Doulgeris, A. P. and Eltoft, T. (2009). Estimation of the
equivalent number of looks in polarimetric synthetic aperture radar imagery.
*IEEE Transactions on Geoscience and Remote Sensing*, 47(11), 3795-3809.

## Choices that change the result

The incidence angle comes from a terrain-correction output band. It's requested
through pyroSAR's `export_extra`, so SNAP writes it on the same grid as the
backscatter. Reading the tie-point incidence grid out of the SNAP intermediate
directory instead gives you a different grid, from before terrain correction.
Those numbers can look perfectly plausible and still be in the wrong place.
`sarpanel doctor` warns you if the installed pyroSAR can't write the band.

The catalog is queried once. `plan` writes a manifest and the workers read their
assigned rows from it. Searching again inside every worker can skip or repeat
scenes, because the result order isn't stable.

Filenames include the scene ID. Two granules can share a date and an orbit
direction, so naming files by date alone would drop one of them.

Backscatter is processed in linear power. The pixel output keeps both linear
power and its decibel conversion in dated columns. For any summary you build
later, average the linear power first and convert that mean to decibels.
Averaging decibels biases the result low. The package refuses `scaling: dB` at
geocode time.

A finished scene is detected before it gets downloaded again. Running `run`
again picks up unfinished scenes after checking the pixel-only schema.

SLC and GRD both work. `product_type: GRD` is the right first test, with smaller
downloads and faster processing. `product_type: SLC` keeps phase and gives you
explicit control over multilooking, at roughly five times the download volume.
For SLC, set `search.relative_orbits` to the tracks you actually want, since
different tracks have different viewing geometry.

Speckle filtering is off by default. A speckle filter changes the
equivalent-number-of-looks estimate that the standard errors depend on.

## Tests

The tests cover radiometry, extraction, AOI parsing, CLI behavior, the manifest,
and provenance. They don't need SNAP or Earthdata credentials.

```powershell
conda run --no-capture-output -n sarpanel python -m pytest -q
```

The suite includes Unix token-file permission tests that can fail on Windows,
because `chmod(600)` doesn't set Unix mode-600 permissions there. Use an
environment token on Windows. Those failures don't mean token authentication is
broken.

## Troubleshooting

| What you see | What to check |
|---|---|
| `environment.yml` not found, or pip says there's no `pyproject.toml` | Change into the cloned repository, not the folder that only holds your notebooks or input data. |
| A long `Solving environment` step | Use `--solver libmamba -v` to see progress. The first solve takes a while. If it stalls, save the output before interrupting, and avoid running installs at the same time. |
| An unknown `libmamba` solver | Check your conda install against the current conda documentation. The environment-creation command needs that solver available. |
| Versions or warnings coming from base Anaconda | Use `conda run -n sarpanel python ...` for installing and running, and check `sys.executable` before installing anything else. |
| `ModuleNotFoundError: sarpanel` inside the new environment | Run the step-2 editable install with the explicit environment prefix. Installing in base doesn't install it in `sarpanel`. |
| Missing `__version__`, with `sarpanel.__file__` showing `None` | Python is finding the outer repository folder instead of the installed package. Use the project environment and its editable install. |
| `No module named 'osgeo'`, `geopandas`, or `rasterio` | Check the interpreter first. If the environment really is missing the dependencies, look back at the environment-creation output. |
| `pg_config executable not found` during a pip install | Something is trying to build from source. Use the conda dependency stack in this guide, then install sarpanel with `--no-deps`. |
| `gpt MISSING` on Windows | Check `Test-Path "$env:SNAP_HOME\bin\gpt.exe"` and `gpt -h`. The doctor check can look for an extensionless `gpt`. |
| `gpt -h` fails | Check that SNAP and its Microwave Toolbox are installed, that SNAP_HOME is the install folder, and that its bin directory is on PATH in this terminal. |
| `No SLURM job detected` during a local run | A cluster-oriented warning, not a processing error. Watch the SNAP logs that follow and the final scene status. |
| `Invalid/Expired token` or rejected credentials | Set a current token in the same terminal, with no surrounding whitespace. |
| `Pre authorization required` | Authorize the ASF data-access application using the instructions or the resolution URL in the error. |
| A credential-file `PermissionError` on Windows | Use `EARTHDATA_TOKEN` in the environment instead of a Unix-permission credential file. |
| Login connection resets | Check network, firewall, VPN, or proxy access to `urs.earthdata.nasa.gov`. |
| `plan` finds no scenes | Check longitude and latitude order, input CRS, dates, orbit filters, and coverage. Try a wider date range and plan again. |
| `export_extra: False` | The installed pyroSAR can't export the incidence band. Check or upgrade it in the project environment. |
| No saved status while processing | Status is saved when a shard finishes. Use the running terminal log to follow the first run. |
| SNAP is killed or runs out of memory | Shrink the study area, keep AOI subsetting on, or set `snap_tile_cache_gb` and `snap_threads` for your machine. Bigger jobs may just need more RAM. |
| `no_data` scenes | Some edge overlaps have too few valid pixels. Check the footprint and the `min_valid_pixels` setting. |

## Authors

sarpanel is written and maintained by Ryan Jayne
([ORCID 0009-0001-4662-9686](https://orcid.org/0009-0001-4662-9686)), with
contributions from [Dev Jyoti Ghosh Arnab](https://www.linkedin.com/in/dev-arnab/).

It came out of SAR-based agricultural and land-use research, but the design is
deliberately domain-neutral. The package knows about radar geometry and
radiometry and nothing about what the polygons handed to it represent. Issues
and pull requests are welcome, including from fields it wasn't originally built
for.

### Citation

If you use sarpanel, please cite it. `CITATION.cff` in the repository root has
the machine-readable version, which GitHub turns into a "Cite this repository"
link.

## License

[MIT](LICENSE)
