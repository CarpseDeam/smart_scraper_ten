# Live Tennis Score Scraper & API

This project provides a complete solution for scraping live ITF tennis match data from Tenipo and exposing it through a clean, modern FastAPI application. It is designed to be resilient, scalable, and easy to maintain.

## 🌟 Highlights

*   **Live Data:** Fetches and serves live, up-to-the-minute tennis match data.
*   **Smart Scraping:** Uses Selenium-Wire to intercept and decode dynamic data requests, avoiding brittle UI-based scraping.
*   **Async Background Polling:** A continuous background task keeps the match data fresh without blocking API requests.
*   **Data Caching:** In-memory caching ensures fast API responses and reduces redundant scraping.
*   **Clean API:** A FastAPI interface provides simple endpoints to access all live data or data for a specific match.
*   **Configuration Driven:** Easily change target URLs and other settings via a `.env` file.
*   **Thoroughly Tested:** Unit and integration tests ensure data mappers and API endpoints are reliable.

## ⬇️ Getting Started

These instructions will get you a copy of the project up and running on your local machine for development and testing purposes.

### Prerequisites

You will need Python 3.8+ and pip installed. You will also need Google Chrome installed, as the scraper uses it in headless mode.

### Installation

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd <your-repo-name>
    ```

2.  **Create and activate a virtual environment (recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
    ```

3.  **Install the required dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Create a configuration file:**
    Create a file named `.env` in the root of the project. You can copy the defaults from `config.py` if you don't need to change anything.
    ```env
    # .env (optional, you can rely on defaults)
    LIVESCORE_PAGE_URL="https://tenipo.com/livescore"
    LIVE_FEED_DATA_URL="https://tenipo.com/xmlko/live2.xml"
    ```

### Running the Application

Once the setup is complete, you can run the application using Uvicorn:

```bash
uvicorn main:app --reload
```
The API will be available at http://127.0.0.1:8000. You can access the interactive API documentation (provided by Swagger UI) at http://127.0.0.1:8000/docs.
⚙️ Configuration
The application's behavior is controlled by environment variables, which are managed by config.py. You can override the default values by creating a .env file in the project root.
LIVESCORE_PAGE_URL: The page the scraper visits to initialize itself and its decoding scripts.
LIVE_FEED_DATA_URL: The URL for the main XML data feed that lists all live matches.
MATCH_XML_URL_TEMPLATE: The template for fetching a specific match's XML data.
USER_AGENT: The User-Agent string the scraper uses.
🚀 API Endpoints
The following endpoints are available once the application is running. The data is served from a cache that is updated automatically in the background approximately every 30 seconds.
Get All Live ITF Data
Returns data for all currently live ITF matches found by the scraper.
URL: /all_live_itf_data
Method: GET
Success Response (200 OK):
```
{
  "cache_last_updated_utc": "2025-07-28T23:30:00.123456+00:00",
  "cache_age_seconds": 15,
  "match_count": 5,
  "matches": [
    {
      "tournament": "ITF M25 La Nucia",
      "round": "First Round",
      "players": [...],
      "score": {...},
      ...
    }
  ]
}
```
Error Response (503 Service Unavailable): Returned if the cache has not been populated ye
```
{
  "detail": "Cache is currently empty. Please try again in a moment."
}
```

Get Specific Match Data
Returns cached data for a single match, identified by its ID.
URL: /match/{match_id}
Method: GET
Success Response (200 OK):
 ```  
    {
      "tournament": "ITF M25 La Nucia",
      "round": "First Round",
      "players": [...],
      ...
    }
```
Error Response (404 Not Found): Returned if the match ID does not exist in the cache.
```
{
  "detail": "Data for match ID {match_id} not found in the live cache."
}
```
🧪 Running Tests
To run the automated tests, use pytest:

```bash
  pytest
```
