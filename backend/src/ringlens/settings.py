"""Runtime configuration. Every value can be overridden with a RINGLENS_* environment variable."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RINGLENS_", env_file=".env", extra="ignore")

    mongo_uri: str = "mongodb://127.0.0.1:27017/?directConnection=true"
    mongo_db: str = "ringlens"
    truth_db: str = "ringlens_truth"

    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "ringlens-dev"
    neo4j_db: str = "neo4j"

    # Where Graph Data Science runs: "plugin" (local Neo4j with GDS) or "aura" (AuraDB Free +
    # an Aura Graph Analytics session; needs Aura API credentials).
    gds_mode: str = "plugin"
    aura_client_id: str = ""
    aura_client_secret: str = ""
    aura_project_id: str = ""
    aura_session_name: str = "ringlens-analytics"
    aura_session_memory: str = "2GB"

    # Hosted demo (one process): run the CDC worker inside the API, and on first start seed
    # this profile and run the analytics so the console has data. Empty = off.
    embed_cdc: bool = False
    bootstrap_profile: str = ""
    bootstrap_seed: int = 1

    # CDC micro-batching: flush after this many events or this many milliseconds.
    cdc_batch_events: int = 1000
    cdc_batch_ms: int = 250

    # A referral edge must repeat at least this often, across this many distinct patients,
    # before it counts towards ring detection. One-off referrals are ordinary noise.
    strong_min_referrals: int = 6
    strong_min_patients: int = 3
    # Ring candidates (SCCs of 3+ providers) at or above this suspicion are reported as rings.
    ring_threshold: float = 0.5

    # Providers whose fused score (0-100) reaches this value are flagged for review.
    flag_threshold: float = 50.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
