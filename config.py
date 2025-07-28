from pydantic import HttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Manages application configuration using Pydantic."""

    # This is the page we visit to trigger the JavaScript
    LIVESCORE_PAGE_URL: HttpUrl = Field(
        default="https://tenipo.com/livescore",
        description="The main page to navigate to, which triggers the data requests."
    )

    # THIS IS THE CORRECTED URL for the data file itself!
    LIVE_FEED_DATA_URL: str = Field(
        default="https://tenipo.com/xmlko/live2.xml",
        description="The full URL to the XML data feed that lists all live matches."
    )

    MATCH_XML_URL_TEMPLATE: str = Field(
        default="https://tenipo.com/xmlko/match{match_id}.xml",
        description="URL template for fetching a specific match. Note the '/xmlko/' path."
    )

    USER_AGENT: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")