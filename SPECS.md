# Technical Specifications: Live Tennis Scraper

## 1. Project Overview

This document outlines the technical specifications for the Live Tennis Scraper, a backend service designed to provide real-time data for ITF (International Tennis Federation) tennis matches.

The primary goal of this project is to create a reliable, scalable, and maintainable system that continuously scrapes data from the `tenipo.com` website, transforms it into a structured format, and makes it available via a REST API. The system is engineered for high availability and includes robust mechanisms for data lifecycle management and operational monitoring.

## 2. Core Requirements

### Functional Requirements

-   **FR1:** The system must periodically scan `tenipo.com` to discover all currently live ITF tennis matches.
-   **FR2:** For each live match, the system must scrape detailed information, including player details, live scores (sets, games, points, tie-breaks), match statistics, point-by-point history, and head-to-head records.
-   **FR3:** All scraped data must be transformed into a consistent, well-defined JSON structure (see Data Contract).
-   **FR4:** The system must expose a public REST API with endpoints to retrieve all live matches and to query for a specific match by its ID.
-   **FR5:** The system must differentiate between "LIVE" and "COMPLETED" matches and manage their lifecycle accordingly.
-   **FR6:** Completed match data must be moved from the active collection to a historical archive to maintain performance.

### Non-Functional Requirements

-   **NFR1 (Reliability):** The system must be resilient to temporary scraping failures and website changes. It must reliably handle matches that disappear and reappear from the source feed without incorrect data loss.
-   **NFR2 (Scalability):** The architecture must support horizontal scaling. It should be possible to run multiple instances of the application behind a load balancer to handle high API traffic.
-   **NFR3 (Performance):** API responses should be fast. Data should be served from an in-memory cache that is refreshed from the database, not from live scrapes. The background polling interval should be configurable.
-   **NFR4 (Maintainability):** The codebase must be modular, adhering to the Single Responsibility Principle, with a clear separation between scraping, data transformation, database interaction, and API layers.
-   **NFR5 (Monitoring):** The system must proactively monitor itself for operational issues, specifically detecting when a live match's score has not changed for an extended period, and send alerts to a designated channel.

## 3. System Architecture

The application is a multi-component system designed for concurrent operation and reliability.

### 3.1. API Layer (FastAPI)

-   Provides the public-facing HTTP endpoints.
-   Serves all data from an in-memory cache (`live_data_cache`) that is managed by the background service. This ensures API requests are fast and do not trigger new scraping tasks.
-   Built with FastAPI for its high performance and automatic OpenAPI documentation.

### 3.2. Background Service

-   The core orchestrator of all background tasks.
-   **Leader Election:** Upon startup in a multi-worker environment (like Gunicorn), all instances perform a leader election using a file lock (`/tmp/scraper_leader.lock`). Only the "leader" process is permitted to start the background service, preventing redundant scraping. Follower processes only serve API requests.
-   **Polling Loop:** The leader's service runs a continuous, asynchronous loop that orchestrates the entire data pipeline at a configurable interval (`CACHE_REFRESH_INTERVAL_SECONDS`).

### 3.3. Scraping Layer (Selenium)

-   **Main Scraper:** A single Selenium instance responsible for visiting the main livescore page to discover all currently live matches.
-   **Worker Pool:** A fixed-size pool of Selenium instances (`CONCURRENT_SCRAPER_LIMIT`) is used to fetch detailed data for individual matches in parallel. This significantly speeds up the data collection phase.
-   **Data Interception:** The scrapers use an advanced technique to intercept background XMLHttpRequests made by the target website. This is more efficient and reliable than parsing HTML, as it captures the raw data feeds directly.

### 3.4. Data Persistence (MongoDB)

-   **`tenipo` collection:** Stores the full data for all *active* matches. This collection is treated as the single source of truth for the API cache.
-   **`tenipo_history` collection:** An archive for all matches that have been confirmed as completed. This keeps the active collection small and fast.
-   **Indexes:** The `tenipo` collection is indexed on `score.status` and `timePolled` to optimize queries for the archiver and garbage collector.

### 3.5. Data Lifecycle & Archiving

-   **The "Quarantine Zone":** This is a key architectural pattern to ensure data reliability. When a match disappears from the live feed, it is not immediately archived. Instead, it enters a "quarantine" state for a defined period (`QUARANTINE_PERIOD`).
    -   If the match reappears in the feed within this period (e.g., after a short network glitch on the source website), it is released from quarantine.
    -   If the match remains absent for the entire period, it is considered definitively finished and is safely moved to the `tenipo_history` collection by the `MongoArchiver`.
-   **Garbage Collection:** A secondary safety mechanism periodically archives any match documents that have not been updated for an extended period of time (15 minutes).

## 4. Data Contract

The final JSON object for a single match, as served by the API and stored in the database, adheres to the following structure.

```json
{
  "_id": "string (Unique Match ID)",
  "match_url": "string (URL to the match page)",
  "tournament": "string (e.g., 'ITF W35 Santo Domingo')",
  "round": "string (e.g., 'Final', 'Semifinal')",
  "timePolled": "string (ISO 8601 UTC timestamp)",
  "players": [
    {
      "name": "string",
      "country": "string (e.g., 'USA')",
      "ranking": "integer | null"
    },
    {
      "name": "string",
      "country": "string",
      "ranking": "integer | null"
    }
  ],
  "score": {
    "sets": [
      {
        "p1": "integer (Player 1 set score)",
        "p2": "integer (Player 2 set score)",
        "p1_tiebreak": "integer | null",
        "p2_tiebreak": "integer | null"
      }
    ],
    "currentGame": {
      "p1": "string (e.g., '15', '40', 'AD')",
      "p2": "string"
    },
    "status": "string ('LIVE' or 'COMPLETED')"
  },
  "matchInfo": {
    "court": "string | null",
    "started": "string (ISO 8601 UTC timestamp) | null"
  },
  "statistics": [
    {
      "groupName": "string ('Service' or 'Return')",
      "statisticsItems": [
        {
          "name": "string (e.g., 'Aces')",
          "home": "string (Player 1 value)",
          "away": "string (Player 2 value)"
        }
      ]
    }
  ],
  "pointByPoint": [
    {
      "game": "string (e.g., '1-0')",
      "point_progression_log": ["string (e.g., '0-15')"]
    }
  ],
  "h2h": [
    {
      "year": "string",
      "event": "string",
      "surface": "string",
      "score": "string"
    }
  ]
}