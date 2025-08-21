# Live Tennis Scraper API

[![Python Version](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/framework-FastAPI-green.svg)](https://fastapi.tiangolo.com/)
[![Database](https://img.shields.io/badge/database-MongoDB-brightgreen.svg)](https://www.mongodb.com/)
[![Scraping Engine](https://img.shields.io/badge/scraper-Selenium-orange.svg)](https://www.selenium.dev/)

A robust, real-time API for scraping and serving live ITF tennis match data.

## Overview

This application provides a reliable backend service that continuously scrapes live tennis match data from `tenipo.com`. It is built with a scalable, multi-worker architecture that ensures only one instance is actively scraping at any time, while multiple API instances can serve data concurrently. The data is parsed, validated, stored in MongoDB, and exposed via a clean RESTful API built with FastAPI.

The system includes built-in monitoring to detect stalled matches and a sophisticated data lifecycle management system to archive completed matches, keeping the active database clean and performant.

## Key Features

-   **Real-time Data:** A background service polls for new data every 15 seconds.
-   **High-Performance API:** Built with FastAPI for modern, asynchronous request handling.
-   **Robust Scraping:** Uses a pool of headless Chrome instances via Selenium to handle modern, JavaScript-heavy websites.
-   **Scalable Architecture:** Implements a leader-election system to support running multiple application workers (e.g., with Gunicorn) without data duplication.
-   **Reliable Data Lifecycle:** A "Quarantine Zone" system prevents premature data archiving if a match temporarily disappears from the source feed.
-   **Proactive Monitoring:** Automatically detects matches that haven't updated in a set time and sends alerts via Telegram.
-   **Persistent Storage:** All active and historical match data is stored in a MongoDB database.

## Architecture Diagram

```mermaid
graph TD
    subgraph "Web Server (e.g., Gunicorn)"
        W1[Worker 1 - LEADER]
        W2[Worker 2 - FOLLOWER]
        W3[Worker 3 - FOLLOWER]
    end

    subgraph "Client"
        U[User/Application]
    end

    LB[Load Balancer]
    DB[(MongoDB)]
    TG[Telegram API]
    SITE[tenipo.com]

    U --> LB
    LB --> W1
    LB --> W2
    LB --> W3

    W1 -- Manages --> Service[Background Scraping Service]
    W2 -.-> Service
    W3 -.-> Service

    Service --> |1. Polls for summary| SITE
    Service --> |2. Dispatches tasks| Pool[Scraper Worker Pool]
    Pool --> |3. Fetches match details| SITE
    Pool --> |4. Transforms data| Mapper[Data Mapper]
    Mapper --> |5. Upserts to DB| DB

    Service --> |Checks for stalls| Monitor[Stall Monitor]
    Monitor --> |Sends alerts| TG

    Service --> |Checks for finished matches| Archiver[Archiver]
    Archiver --> |Moves data to history| DB

    W1 -- Reads cache from --> DB
    W2 -- Reads cache from --> DB
    W3 -- Reads cache from --> DB
    
Setup and Installation
1. Prerequisites
Python 3.10+
MongoDB instance
Google Chrome or Chromium browser installed on the host machine.
2. Clone Repository
```
```bash
    git clone https://github.com/edgeAItennis/scraper.git
    cd scraper
```
Set Up Virtual Environment
```bash
    python3 -m venv venv
    source venv/bin/activate
```
 Install Dependencies
```bash
  pip install -r requirements.txt
```
Configure Environment Variables
```bash
  cp .env.example .env
```
Now, edit the .env file with your specific settings. You must provide the Telegram credentials
for monitoring to work
```bash
  # .env
    MONGO_URI="mongodb://localhost:27017"
    MONGO_DB_NAME="edgeAI"
    
    # REQUIRED FOR MONITORING
    TELEGRAM_BOT_TOKEN="your_secret_bot_token_here"
    TELEGRAM_CHAT_ID="your_target_chat_id_here"
```
Running the Application
Use uvicorn for development or a production-ready server like gunicorn for deployment.
The application is designed to run with multiple workers. 
The leader-election system will automatically ensure only one worker runs the background scraping tasks.
```bash
    # Run with 3 workers
    gunicorn -w 3 -k uvicorn.workers.UvicornWorker main:app --bind 0.0.0.0:8000
```

Once running, the API documentation will be available at http://localhost:8000/docs.
API Endpoints
Get All Live Matches
URL: /all_live_itf_data
Method: GET
Description: Retrieves a consolidated list of all currently live ITF matches from the in-memory cache.
Success Response (200 OK)

```bash
    {
      "cache_last_updated_utc": "2025-08-21T14:20:00.123456+00:00",
      "cache_age_seconds": 5,
      "match_count": 1,
      "matches": [
        {
          "_id": "1234567",
          "match_url": "https://tenipo.com/match/-/1234567",
          "tournament": "ITF M25 Anapoima",
          "round": "Final",
          "timePolled": "2025-08-21T14:19:55.987654+00:00",
          "players": [
            { "name": "Player A", "country": "USA", "ranking": 150 },
            { "name": "Player B", "country": "CAN", "ranking": 180 }
          ],
          "score": {
            "sets": [
              { "p1": 6, "p2": 4, "p1_tiebreak": null, "p2_tiebreak": null },
              { "p1": 2, "p2": 3, "p1_tiebreak": null, "p2_tiebreak": null }
            ],
            "currentGame": { "p1": "30", "p2": "40" },
            "status": "LIVE"
          },
          "...": "more fields"
        }
      ]
    }
```
Get Specific Match by ID
URL: /match/{match_id}
Method: GET
Description: Retrieves the complete data for a single match by its unique ID.
Success Response (200 OK):

```bash
    {
      "_id": "1234567",
      "match_url": "https://tenipo.com/match/-/1234567",
      "tournament": "ITF M25 Anapoima",
      "round": "Final",
      "timePolled": "2025-08-21T14:19:55.987654+00:00",
      "players": [
        { "name": "Player A", "country": "USA", "ranking": 150 },
        { "name": "Player B", "country": "CAN", "ranking": 180 }
      ],
      "score": {
        "sets": [
          { "p1": 6, "p2": 4, "p1_tiebreak": null, "p2_tiebreak": null },
          { "p1": 2, "p2": 3, "p1_tiebreak": null, "p2_tiebreak": null }
        ],
        "currentGame": { "p1": "30", "p2": "40" },
        "status": "LIVE"
      },
      "matchInfo": {
        "court": "Court 1",
        "started": "2025-08-21T13:05:00+00:00"
      },
      "statistics": [
        {
          "groupName": "Service",
          "statisticsItems": [
            { "name": "Aces", "home": "5", "away": "3" }
          ]
        }
      ],
      "pointByPoint": [
        {
          "game": "1-0",
          "point_progression_log": ["0-15", "15-15", "30-15", "40-15", "Game Player A"]
        }
      ],
      "h2h": []
    }
```

