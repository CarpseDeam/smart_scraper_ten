# config.py
from pydantic import HttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Manages application configuration using Pydantic."""

    # --- Scraper Settings ---
    LIVESCORE_PAGE_URL: HttpUrl = Field(
        default="https://tenipo.com/livescore",
        description="The main page to navigate to, which triggers the data requests."
    )
    MATCH_XML_URL_TEMPLATE: str = Field(
        default="https://tenipo.com/xmlko/match{match_id}.xml",
        description="URL template for fetching a specific match's XML data."
    )
    USER_AGENT: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    # --- Two-Speed Polling Settings ---
    FAST_POLL_INTERVAL_SECONDS: int = Field(
        default=4,
        description="FAST LANE: Interval in seconds for lightning-fast live score updates."
    )
    SLOW_POLL_INTERVAL_SECONDS: int = Field(
        default=45,
        description="SLOW LANE: Interval in seconds for detailed data enrichment (stats, H2H, etc.)."
    )

    # --- Legacy Setting (for backward compatibility) ---
    CACHE_REFRESH_INTERVAL_SECONDS: int = Field(
        default=3,
        description="DEPRECATED: Use FAST_POLL_INTERVAL_SECONDS instead."
    )

    CONCURRENT_SCRAPER_LIMIT: int = Field(
        default=5,
        description="Maximum number of detail scrapers to run simultaneously for the slow lane."
    )

    # --- Database Settings ---
    MONGO_URI: str = Field(
        default="mongodb://localhost:27017",
        description="The full connection string URI for the MongoDB database."
    )
    MONGO_DB_NAME: str = Field(
        default="edgeAI",
        description="The name of the database to use within MongoDB."
    )

    # --- Monitoring Settings (MUST be set in environment) ---
    TELEGRAM_BOT_TOKEN: str = Field(
        description="The secret token for the Telegram bot. MUST be set as an environment variable."
    )
    TELEGRAM_CHAT_ID: str = Field(
        description="The chat ID for the Telegram bot. MUST be set as an environment variable."
    )
    STALL_MONITOR_SECONDS: int = Field(
        default=300,
        description="Duration in seconds after which a match with no score change is considered stalled."
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")