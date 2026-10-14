from datetime import date

from pydantic_settings import BaseSettings, SettingsConfigDict

FIRST_YEAR = 1940  # ERA5 starts here


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "mongo" for anything real. "memory" exists for tests and quick hacking.
    storage: str = "mongo"
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db: str = "climate_hazards"

    # Comma separated list of origins allowed to call the API from a browser.
    cors_origins: str = "http://localhost:5173"

    # Where the real client IP comes from (used for rate limiting). Behind one
    # proxy that appends to X-Forwarded-For, set TRUSTED_PROXY_HOPS=1. If the
    # platform sets a header it controls (e.g. CF-Connecting-IP), name it in
    # CLIENT_IP_HEADER instead. Both default to "trust nothing".
    trusted_proxy_hops: int = 0
    client_ip_header: str = ""

    user_agent: str = "climate-hazard-trends/1.0 (+https://github.com/CapricornJay/climate-hazard-trends)"

    # Our share of Open-Meteo's free tier (600/min, 5000/h, 10000/day per IP),
    # in their weighted call units. Kept under the real limits on purpose.
    openmeteo_minute_budget: float = 550
    openmeteo_hour_budget: float = 4500
    openmeteo_day_budget: float = 9000

    max_region_points: int = 9

    # Per client IP
    rate_general_per_min: int = 120
    rate_analysis_per_min: int = 20
    rate_upstream_per_hour: int = 15  # analyses that need new data from upstream
    rate_export_per_min: int = 15

    # Serve the built frontend from this directory (docker-compose setup).
    static_dir: str = ""

    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


def last_complete_year(today: date | None = None) -> int:
    """Most recent year that can be analysed.

    ERA5 shows up ~5 days behind real time as preliminary "ERA5T" data and is
    only finalised 2-3 months later, so last December isn't trustworthy until
    around mid-March. Only offering finished years also means cached series
    never go stale.
    """
    today = today or date.today()
    return today.year - 1 if today >= date(today.year, 3, 15) else today.year - 2
