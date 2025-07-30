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

    # --- Database Settings ---
    MONGO_URI: str = Field(
        default="mongodb://localhost:27017",
        description="The full connection string URI for the MongoDB database."
    )
    MONGO_DB_NAME: str = Field(
        default="tennis_livescores",
        description="The name of the database to use within MongoDB."
    )

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")