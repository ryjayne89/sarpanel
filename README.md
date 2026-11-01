# sarpanel

Analysis-ready **Sentinel-1** observations over polygons, written to **Parquet**,
with the acquisition geometry and speckle statistics kept alongside the
backscatter.

Give the program a polygon (or many polygons) and a date range. It queries the
Alaska Satellite Facility (ASF) archive once, processes the matching Sentinel-1
scenes with [ESA SNAP](https://step.esa.int/main/toolboxes/snap/) through
[pyroSAR](https://github.com/johntruckenbrodt/pyroSAR), and writes **one row per
polygon per acquisition**. Each row has the backscatter estimate and the
information a statistical model needs in order to weight that estimate.

An earlier version of this tool was called `sar2parquet` and wrote one row per
pixel. This version writes one row per polygon per acquisition. The repository,
the Python import, and the command-line program all use the name `sarpanel`.

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
git clone https://github.com/Dev-Arnab/sarpanel.git
cd sarpanel
conda env create -f environment.yml --solver libmamba -v
```

You must be inside the cloned repository: it contains both `environment.yml`
and `pyproject.toml`. A directory containing only notebooks or field data is
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
no field file required. For your own study, copy it first:

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
  granularity: polygon

n_shards: 1
```

| Setting | What to change |
|---|---|
| `name` | Choose a short run name, such as `my_fields_2020`. |
| `aoi.source` | Enter your bounding box in longitude/latitude, or the path to your polygon file. |
| `search.start_date`, `search.end_date` | Set the study period using quoted `YYYY-MM-DD` dates. Start with a short range. |
| `processing.target_crs` | Choose a projected CRS in metres suitable for your area. The example's `EPSG:32616` is UTM zone 16N; it is not a global default. |
| `processing.target_spacing_m` | Output pixel spacing in metres. Start with `10.0` for this GRD workflow. |
| `output.root` | Choose a new result directory for each different area, date range, or processing configuration. |
| `output.granularity` | Keep `polygon` for one summary per polygon per scene; `pixel` creates much larger tables. |
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
  source: "D:/fields/my_fields.gpkg"
  id_column: "field_id"  # Replace with an actual column in your file.
```

GeoPackage, GeoJSON, Shapefile, and GeoParquet inputs are supported. A WKT
polygon string can also be used as `source`. File inputs must have a CRS.
Use forward slashes in Windows YAML paths, as above, to avoid backslash escapes
inside double-quoted strings.

`id_column` selects the column identifying your fields. It is optional: when
omitted, sarpanel looks for a suitable identifier or generates one. Optional
`layer`, `where`, and `min_area_m2` settings select a layer, filter attributes,
and exclude small polygons. For example, `where: "farm_name == 'my_farm'"`
requires a `farm_name` column in the input file.

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
runs/local_demo/
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
conda run --no-capture-output -n sarpanel python -c "import pandas as pd; df = pd.read_parquet('runs/local_demo/local_demo_observations.parquet'); print(df[['date', 'relative_orbit', 'inc_angle_mean', 'n_pixels_valid', 'enl_empirical', 'VV_mean_db', 'VV_se_db']].head())"
```

Observation rows use **`date`**; the scene manifest uses `acquisition_date`.
The example path above is for the demo. Your combined table is written to
`<output.root>/<name>_observations.parquet`.

For a Michigan cropland smoke test, populated incidence angles around 29–46
degrees, finite ENL for sufficiently large valid samples, and substantial valid
pixel counts are useful initial checks. Expected backscatter depends on the
land cover, season, and processing; an error-free run is not a substitute for
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

df = pd.read_parquet("runs/local_demo/local_demo_observations.parquet")
print(df[["date", "relative_orbit", "inc_angle_mean", "VV_mean_db"]].head())
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
| `aggregate CONFIG` | Combines per-scene Parquet files into one table |

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

## What a row contains

| Field | Meaning |
|---|---|
| `VV_mean_db`, `VH_mean_db` | backscatter, averaged in linear power and then converted to decibels |
| `inc_angle_mean` / `std` / `min` / `max` | terrain-corrected projected local incidence angle |
| `looks_nominal` | multilooking implied by the processing configuration |
| `enl_empirical` | equivalent number of looks estimated from the data |
| `enl_efficiency` | ratio of the two, i.e. how much independence the processing retained |
| `n_pixels_valid` | pixels that survived nodata, layover, and shadow masking |
| `*_se_db` | standard error of the polygon mean |
| `*_cv` | linear-domain coefficient of variation (texture) |
| `relative_orbit`, `orbit_direction` | which track viewed the polygon, and whether the pass was ascending or descending |
| `layover_shadow_fraction` | share of the polygon that is not a real measurement |

A two-hectare plot and a hundred-hectare field observed on the same day do not
have the same precision. Precision depends on how many independent looks went
into the mean. Different tracks also arrive at different incidence angles, and
backscatter depends on that angle. `sarpanel` keeps those quantities so a later
model can use them.

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

**Means are computed in linear power.** Averaging decibels pulls the result
low, and the pull is larger where the field is more heterogeneous. The package
refuses `scaling: dB` at geocode time. Decibels are applied when the polygon
mean is reported.

**A finished scene is detected before it is downloaded again.** Rerunning
`run` continues the unfinished scenes.

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
| `environment.yml` not found, or pip says no `pyproject.toml` | Change into the cloned repository, not the folder containing only your notebooks or field data. |
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

`sarpanel` is developed by **Dev Jyoti Ghosh Arnab** and **Ryan Jayne**.

The package is a joint effort to turn a Sentinel-1 processing workflow into an
installable tool: search, SNAP processing, and polygon-level tables with
geometry and speckle statistics included.

## License

[MIT](LICENSE)
