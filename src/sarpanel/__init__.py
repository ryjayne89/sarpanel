"""
sarpanel: Sentinel-1 pixel time series within AOI polygons.

Produces one row per pixel, with dated columns for each acquisition's
backscatter, incidence angle, mask, and metadata. Aggregation outer-joins
acquisitions on a shared projected pixel grid. Polygon summaries are not
supported.
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
