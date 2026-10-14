import re
import unicodedata
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from .config import FIRST_YEAR, last_complete_year
from .hazards import HAZARDS

MAX_BOX_DEG = 20.0
MIN_YEARS = 10


def clean_text(value: str) -> str:
    """Strip control/format characters and collapse whitespace. Labels end up
    in PDFs and HTML, and search terms in upstream query strings."""
    value = "".join(ch for ch in value if unicodedata.category(ch)[0] != "C")
    return re.sub(r"\s+", " ", value).strip()


Label = Annotated[str, Field(max_length=200), AfterValidator(lambda s: clean_text(s)[:120] or None)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PointIn(_Strict):
    kind: Literal["point"]
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    label: Label | None = None


class BoxIn(_Strict):
    kind: Literal["box"]
    south: float = Field(ge=-90, le=90, allow_inf_nan=False)
    north: float = Field(ge=-90, le=90, allow_inf_nan=False)
    west: float = Field(ge=-180, le=180, allow_inf_nan=False)
    east: float = Field(ge=-180, le=180, allow_inf_nan=False)
    label: Label | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.north <= self.south:
            raise ValueError("north must be greater than south")
        if self.east <= self.west:
            raise ValueError("east must be greater than west (boxes crossing the antimeridian aren't supported)")
        if self.north - self.south > MAX_BOX_DEG or self.east - self.west > MAX_BOX_DEG:
            raise ValueError(f"a box can span at most {MAX_BOX_DEG:g} degrees in each direction")
        return self


class AdminIn(_Strict):
    kind: Literal["admin"]
    osm_type: Literal["R", "W"]
    osm_id: int = Field(gt=0, lt=10**12)
    label: Label | None = None


class AnalysisIn(_Strict):
    region: Annotated[PointIn | BoxIn | AdminIn, Field(discriminator="kind")]
    hazard: Literal["heatwave", "heavy_rain"] = "heatwave"
    source: Literal["era5", "ghcn"] = "era5"
    start_year: int
    end_year: int
    baseline_start: int = 1961
    baseline_end: int = 1990
    percentile: float = Field(95.0, ge=80, le=99.5, allow_inf_nan=False)
    min_duration: int | None = None
    threshold_mode: Literal["fixed", "doy"] = "fixed"

    @model_validator(mode="after")
    def _check(self):
        last = last_complete_year()
        for name in ("start_year", "end_year", "baseline_start", "baseline_end"):
            v = getattr(self, name)
            if not FIRST_YEAR <= v <= last:
                raise ValueError(f"{name} must be between {FIRST_YEAR} and {last}")
        if self.end_year - self.start_year + 1 < MIN_YEARS:
            raise ValueError(f"pick at least {MIN_YEARS} years, otherwise a trend means nothing")
        if self.baseline_end - self.baseline_start + 1 < MIN_YEARS:
            raise ValueError(f"the baseline needs at least {MIN_YEARS} years (30 is the WMO standard)")

        hz = HAZARDS[self.hazard]
        if self.min_duration is None:
            self.min_duration = hz.default_min_duration
        lo, hi = hz.min_duration_range
        if not lo <= self.min_duration <= hi:
            raise ValueError(f"min_duration for {self.hazard} must be between {lo} and {hi} days")
        if self.threshold_mode not in hz.threshold_modes:
            raise ValueError(f"threshold_mode '{self.threshold_mode}' isn't available for {self.hazard}")
        if self.source == "ghcn" and self.region.kind != "point":
            raise ValueError("station data (source=ghcn) is only available for a single location")
        self.percentile = round(self.percentile, 1)
        return self
