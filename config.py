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

    # --- Polling Settings ---
    CACHE_REFRESH_INTERVAL_SECONDS: int = Field(
        default=15,
        description="The interval in seconds between each poll for live match data."
    )
    CONCURRENT_SCRAPER_LIMIT: int = Field(
        default=3,
        description="The maximum number of scrapers to run at the same time. Tuned for a Railway Pro plan."
    )

    # --- Database Settings ---
    MONGO_URI: str = Field(
        default="mongodb://localhost:27017",
        description="The full connection string URI for the MongoDB database."
    )
    MONGO_DB_NAME: str = Field(
        default="edgeAI",  # This is the corrected database name from the client.
        description="The name of the database to use within MongoDB."
    )

    # --- Monitoring Settings (MUST be set in environment) ---
    TELEGRAM_BOT_TOKEN: str = Field(
        description="The secret token for the Telegram bot. MUST be set as an environment variable."
    )
    TELEGRAM_CHAT_ID: str = Field(
        description="The chat ID for Telegram alerts. MUST be set as an environment variable."
    )
    STALL_MONITOR_SECONDS: int = Field(
        default=300,
        description="Duration in seconds after which a match with no score change is considered stalled."
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")