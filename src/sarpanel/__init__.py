"""
sarpanel: analysis-ready Sentinel-1 pixel observations within AOI polygons.

By default, produces one record per valid pixel per AOI per scene, carrying
coordinates, linear and decibel backscatter, projected local incidence angle,
nominal looks, layover/shadow status, and orbit track. Polygon summaries with
empirical speckle statistics are available as an explicit option.
"""

__version__ = "0.1.0"

from .aoi import AOISet, load_aoi
from .config import RunConfig
from .extract import SceneContext, SceneReader, extract_scene
from .processing import ProcessingConfig

__all__ = [
    "__version__",
    "AOISet",
    "load_aoi",
    "RunConfig",
    "SceneContext",
    "SceneReader",
    "extract_scene",
    "ProcessingConfig",
]
