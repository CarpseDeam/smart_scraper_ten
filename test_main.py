import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone

# We import the app from your main.py file
from main import app, live_data_cache

# This creates a virtual client to make requests to our app
client = TestClient(app)


def test_get_all_live_data_with_empty_cache():
    """
    Tests that the main endpoint returns a 503 error when the cache
    has not been populated yet.
    """
    # Ensure cache is empty for this test
    live_data_cache["data"] = {}
    live_data_cache["last_updated"] = None

    response = client.get("/all_live_itf_data")
    assert response.status_code == 503
    assert response.json() == {"detail": "Cache is currently empty. Please try again in a moment."}


def test_get_match_data_not_in_cache():
    """
    Tests that the endpoint correctly returns a 404 when a match ID
    is not found in the (currently empty) cache.
    """
    response = client.get("/match/non_existent_id_123")
    assert response.status_code == 404
    assert response.json() == {"detail": "Data for match ID non_existent_id_123 not found in the live cache."}


def test_get_all_live_data_with_populated_cache(monkeypatch):
    """
    Tests a successful request to the main endpoint when the cache is populated.
    'monkeypatch' is a pytest tool to temporarily replace parts of our code.
    """
    # 1. Define the fake data we want to be in the cache
    fake_match_data = {"matchId": "123", "tournament": "Test Open"}

    # 2. "Monkeypatch" the cache to contain our fake data
    monkeypatch.setitem(live_data_cache, "data", {"123": fake_match_data})
    monkeypatch.setitem(live_data_cache, "last_updated", datetime.now(timezone.utc))

    # 3. Call the API endpoint
    response = client.get("/all_live_itf_data")

    # 4. Assert the results
    assert response.status_code == 200
    response_json = response.json()
    assert response_json["match_count"] == 1
    assert response_json["matches"][0]["tournament"] == "Test Open"