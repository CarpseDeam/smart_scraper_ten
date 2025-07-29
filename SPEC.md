Technical Specification
Project: Live Tennis Score Scraper & API
Version: 1.0
Date: 2025-07-28
1. Introduction
1.1. Purpose
This document outlines the technical design, architecture, and implementation details of the Live Tennis Score Scraper & API. The project's primary goal is to reliably extract live ITF tennis match data from the website tenipo.com, transform it into a structured format, and serve it via a high-performance JSON API.
1.2. Scope
The system is responsible for:
Automated, continuous scraping of live match data.
Parsing and decoding proprietary data formats.
Transforming raw scraped data into a client-friendly JSON structure.
Providing API endpoints to access the collected data.
Maintaining a fresh data cache to ensure fast response times.
2. System Architecture
The application is composed of four main components: a smart scraper, a data mapper, a caching layer, and a REST API server.
Smart Scraper (smart_scraper.py): A browser automation module built with selenium-wire that intercepts network traffic to capture data payloads directly, rather than parsing HTML.
Data Mapper (data_mapper.py): A set of functions responsible for transforming the raw XML data from the scraper into the final client-facing JSON format.
Caching & Polling (main.py): An in-memory dictionary acts as a cache. A background asyncio task runs continuously, invoking the scraper to refresh this cache with live data every 30 seconds.
API Server (main.py): A FastAPI application that serves the data stored in the cache through two simple GET endpoints.
3. Core Components
3.1. TenipoScraper
Technology: selenium-wire with a headless Google Chrome instance.
Initialization: The scraper is initialized at application startup and held in a global container to persist the browser session.
Data Interception: It does not scrape visible HTML. Instead, it navigates to the livescore page to load the necessary JavaScript functions into the browser's context. It then uses driver.wait_for_request to intercept the background live2.xml and match{id}.xml data requests.
Decoding: The website encodes its XML data. The scraper leverages the site's own JavaScript function (janko()) by executing it within the browser instance (driver.execute_script) to decode the captured payloads. This is a robust method that is not susceptible to changes in the encoding logic, as long as the function name remains the same.
3.2. Background Polling Task (poll_for_live_data)
Orchestration: This asyncio task, launched at application startup, serves as the main engine of the application.
Workflow:
Calls scraper.get_live_matches_summary() to fetch the list of all currently live matches.
Filters this list to retain only matches with "ITF" in the tournament name.
Iterates through the filtered list of ITF matches.
For each match, it calls scraper.fetch_match_data(match_id) to get the detailed XML data.
The raw data is then passed to transform_match_data_to_client_format.
The resulting formatted JSON is stored in a new dictionary, keyed by match_id.
Finally, it replaces the global live_data_cache with this newly built data set and updates the last_updated timestamp.
Frequency: The loop sleeps for a CACHE_EXPIRATION_SECONDS (default: 30) period between runs.
Error Handling: The entire polling cycle is wrapped in a try...except block to log errors and prevent the background task from crashing.
3.3. Data Mapper (transform_match_data_to_client_format)
Purpose: To act as an anti-corruption layer, decoupling the raw data structure from the API's response structure.
Functionality:
It takes the raw dictionary (converted from XML) as input.
It uses a series of helper functions (_parse_player_info, _parse_stats_string, etc.) to safely extract and clean individual data points.
These helpers are designed to be resilient to missing or empty values, returning None or a sensible default.
It handles complex string parsing for head-to-head (H2H) data and detailed match statistics.
It assembles the final, clean JSON object that the client will receive.
4. API Endpoints
Technology: FastAPI.
Data Source: All endpoints read directly from the live_data_cache global dictionary. They do not trigger scraping actions themselves, ensuring immediate responses.
/all_live_itf_data: Returns a list of all values in the live_data_cache["data"] dictionary. It also includes metadata about the cache's freshness.
/match/{match_id}: Performs a direct lookup in the live_data_cache["data"] dictionary using the provided match_id as the key.
5. Non-Functional Requirements
Configuration: Key URLs and settings are managed in config.py and can be overridden by environment variables via a .env file, as handled by pydantic-settings.
Dependencies: All Python dependencies are listed in requirements.txt.
Testing: The project includes a suite of tests using pytest:
test_data_mapper.py: Unit tests for all data transformation and parsing logic.
test_main.py: Integration tests for the API endpoints, using a TestClient to simulate HTTP requests and monkeypatch to control the state of the cache.
Logging: The application uses Python's built-in logging module to provide detailed information on the scraping process, cache updates, and any errors that occur.