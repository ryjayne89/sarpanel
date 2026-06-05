"""Run the pyroSAR/SNAP geocoding chain on a downloaded Sentinel-1 scene.

pyroSAR is imported lazily so that the rest of the package can be imported and
tested without a working ESA SNAP installation.
"""

from __future__ import annotations

import os
from typing import Optional


def prep_env(java_heap: str = "120G", tmpdir: Optional[str] = None) -> str:
    """Configure headless Java + a temp directory for SNAP, returning the tmpdir.

    Uses ``setdefault`` so any values already present in the environment win.
    """
    opts = f"-Djava.awt.headless=true -Xmx{java_heap}"
    os.environ.setdefault("_JAVA_OPTIONS", opts)
    os.environ.setdefault("SNAP_JAVA_OPTS", opts)
    tmpdir = tmpdir or os.environ.get("TMPDIR") or "/tmp"
    os.environ["TMPDIR"] = tmpdir
    os.environ.setdefault("JAVA_TOOL_OPTIONS", f"-Djava.io.tmpdir={tmpdir}")
    return tmpdir


def geocode_scene(
    *,
    infile: str,
    outdir: str,
    tmpdir: str,
    t_srs: str = "EPSG:4326",
    target_res: float = 10.0,
    polarizations: str = "all",
    scaling: str = "dB",
    remove_thermal_noise: bool = True,
    remove_border_noise: bool = True,
    speckle_filter: bool = False,
):
    """Version-safe wrapper around :func:`pyroSAR.snap.util.geocode`.

    Maps ``target_res`` to either ``tr`` (newer pyroSAR) or ``spacing`` (older),
    and only forwards ``speckleFilter`` when the installed version supports it.
    """
    from inspect import signature
    from pyroSAR.snap.util import geocode as _geocode_raw

    sig = signature(_geocode_raw).parameters
    kwargs = dict(
        infile=infile,
        outdir=outdir,
        t_srs=t_srs,
        polarizations=polarizations,
        scaling=scaling,
        removeS1ThermalNoise=remove_thermal_noise,
        removeS1BorderNoise=remove_border_noise,
        tmpdir=tmpdir,
    )
    if "tr" in sig:
        kwargs["tr"] = target_res
    else:
        kwargs["spacing"] = target_res
    if "speckleFilter" in sig:
        kwargs["speckleFilter"] = speckle_filter

    return _geocode_raw(**kwargs)
