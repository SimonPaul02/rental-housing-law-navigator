"""Runtime configuration, read from the environment."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- app ---
    app_name: str = "Rental Housing Law Navigator"
    environment: str = "development"
    log_level: str = "INFO"

    # --- the challenge's default query date ---
    default_as_of: str = "2026-10-01"

    # --- database (Supabase Postgres) ---
    # Supabase hands you three connection strings. Use the SESSION POOLER:
    #
    #   session pooler      aws-0-<region>.pooler.supabase.com:5432   <- this one
    #   transaction pooler  aws-0-<region>.pooler.supabase.com:6543
    #   direct              db.<ref>.supabase.co:5432
    #
    # Session pooler is IPv4 and keeps prepared statements working, which suits
    # a persistent container holding its own SQLAlchemy pool. The direct
    # connection is IPv6-only unless you buy the IPv4 add-on - it may work from
    # Fly, but it is not worth debugging mid-hackathon. If you do end up on the
    # transaction pooler (6543), set db_disable_prepared_statements=true.
    database_url: str = Field(default="postgresql+asyncpg://postgres:postgres@localhost:5432/rhln")
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_disable_prepared_statements: bool = Field(
        default=False,
        description="Required on the Supabase transaction pooler (port 6543).",
    )

    # --- Anthropic (Module A extraction) ---
    anthropic_api_key: str | None = None
    extraction_model: str = "claude-opus-5"
    extraction_max_tokens: int = 16000
    extraction_concurrency: int = 4

    # --- Module B geocoding ---
    census_geocoder_url: str = "https://geocoding.geo.census.gov/geocoder/geographies/address"
    geocoder_timeout_seconds: float = 20.0
    geocode_concurrency: int = 8

    # --- CORS. The browser normally reaches us through the Next.js rewrite,
    # so this only matters when the frontend is pointed straight at us. ---
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- starter-pack data ---
    # Defaults to the repo root for local work. The Docker image copies
    # corpus/, data/, schema/ and dev/ to /srv/starter-pack and sets
    # DATA_ROOT accordingly, since the repo layout does not exist there.
    data_root: Path = REPO_ROOT

    @property
    def corpus_dir(self) -> Path:
        return self.data_root / "corpus"

    @property
    def corpus_manifest(self) -> Path:
        return self.data_root / "corpus" / "corpus_manifest.csv"

    @property
    def corpus_text_dir(self) -> Path:
        return self.data_root / "corpus" / "text"

    @property
    def addresses_csv(self) -> Path:
        return self.data_root / "data" / "sample_addresses.csv"

    @property
    def rule_schema(self) -> Path:
        return self.data_root / "schema" / "rule_record.schema.json"

    @property
    def change_tests(self) -> Path:
        return self.data_root / "dev" / "change_tests.json"

    @property
    def sync_database_url(self) -> str:
        """Alembic runs synchronously."""
        return self.database_url.replace("+asyncpg", "+psycopg2").replace(
            "postgresql+psycopg2", "postgresql"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
