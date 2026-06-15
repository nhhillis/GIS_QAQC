from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProjectHeader:
    jp_number: Optional[str] = None
    county: Optional[str] = None
    road_number: Optional[str] = None
    water_body_name: Optional[str] = None
    row_date: Optional[str] = None
    let_date: Optional[str] = None
    project_length: Optional[str] = None
    lat_long: Optional[str] = None
    footprint_acres: Optional[float] = None
    section_range_township: Optional[str] = None


@dataclass
class StreamFeature:
    label: str                          # e.g. S1, DF1
    stream_name: Optional[str] = None
    feature_type: Optional[str] = None  # perennial, intermittent, etc.
    jurisdictional_status: Optional[str] = None  # Likely / Unlikely
    acres: Optional[float] = None
    linear_feet: Optional[float] = None
    mapped_on_usgs: Optional[str] = None  # Yes / No


@dataclass
class WetlandFeature:
    label: str                          # e.g. W1, OW1
    wetland_type: Optional[str] = None  # emergent / scrub-shrub / forested / pond / etc.
    cowardin: Optional[str] = None
    jurisdictional_status: Optional[str] = None
    acres: Optional[float] = None


@dataclass
class HabitatFeature:
    label: str                          # e.g. bat_forest, abb_native_veg
    species: Optional[str] = None
    acres: Optional[float] = None
    distance_band: Optional[str] = None  # within 100 ft, 100-300 ft, >300 ft


@dataclass
class BridgeFeature:
    label: str                          # e.g. B1
    nbi_number: Optional[str] = None
    water_body: Optional[str] = None
    road_number: Optional[str] = None


@dataclass
class WWReport:
    source_path: str
    header: ProjectHeader = field(default_factory=ProjectHeader)
    streams: list[StreamFeature] = field(default_factory=list)
    wetlands: list[WetlandFeature] = field(default_factory=list)


@dataclass
class BAReport:
    source_path: str
    header: ProjectHeader = field(default_factory=ProjectHeader)
    footprint_acres: Optional[float] = None
    habitat_features: list[HabitatFeature] = field(default_factory=list)
    bridges: list[BridgeFeature] = field(default_factory=list)


@dataclass
class ShapefileData:
    streams: list[StreamFeature] = field(default_factory=list)
    wetlands: list[WetlandFeature] = field(default_factory=list)
    open_waters: list[WetlandFeature] = field(default_factory=list)
    habitat_features: list[HabitatFeature] = field(default_factory=list)
    bridges: list[BridgeFeature] = field(default_factory=list)
    footprint_acres: Optional[float] = None
    schema_issues: list[str] = field(default_factory=list)


class Severity:
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    INFO = "INFO"


@dataclass
class QCFinding:
    check_name: str
    severity: str           # Severity constant
    message: str
    shapefile_value: Optional[str] = None
    report_value: Optional[str] = None
    feature_id: Optional[str] = None
