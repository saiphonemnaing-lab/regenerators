"""Location and station selection for orchard chill/GDD monitoring.

Quick start::

    from station_selection import (
        OrchardLocation,
        SelectionConfig,
        StationCatalog,
        select_stations,
    )

    catalog = StationCatalog.from_csv("data/cimis_stations.csv")
    result = select_stations(
        OrchardLocation(latitude=39.7285, longitude=-121.8375, elevation_m=60),
        catalog,
    )
    print(result.station_ids)   # e.g. ['244', '222', '250']
    print(result.to_json())     # full selected-station metadata
"""

from .catalog import StationCatalog
from .config import SelectionConfig
from .models import (
    HistoryWindow,
    OrchardLocation,
    SelectionResult,
    Station,
    StationMatch,
)
from .selector import (
    InsufficientStationsError,
    select_for_coordinates,
    select_stations,
)
from .version import __version__

__all__ = [
    "HistoryWindow",
    "InsufficientStationsError",
    "OrchardLocation",
    "SelectionConfig",
    "SelectionResult",
    "Station",
    "StationCatalog",
    "StationMatch",
    "__version__",
    "select_for_coordinates",
    "select_stations",
]
