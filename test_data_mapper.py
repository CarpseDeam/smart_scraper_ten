import pytest
from data_mapper import (
    transform_match_data_to_client_format,
    _parse_player_info,
    _parse_round_info,
    _parse_h2h_string,
    _parse_stats_string
)


# This is a sample of the raw data our scraper produces.
# We copy-pasted this from our successful logs! This is called a "fixture".
@pytest.fixture
def sample_raw_data():
    """Provides a consistent, sample raw data dictionary for tests."""
    return {
        "match": {
            "id": "543206", "tournament_name": "ITF M25 La Nucia", "site": "La Nucia, Spain",
            "surface": "Clay", "court_id": "11", "court_name": "Court 1", "tournament_id": "253101",
            "player1": "Vukic, Aleksandar", "country1": "AUS #99", "player2": "Martinez, Pedro",
            "country2": "ESP #68", "serve": "2", "game1": "30", "game2": "30",
            "set11": "2", "set12": "0", "set21": "1", "set22": "0", "set31": "0", "set32": "0",
            "set41": "0", "set42": "0", "set51": "0", "set52": "0",
            "stat": "1", "winner": "0", "starttime": "1753722600",
            "round": "First Round-USD 23760-10-128",
            "stats": "14:39/3,1,6,,2,3,,,8,5/,,3,3,3,,,,1,1",
            "h2h": "193/105/5:7 4:6 0:6/2/128/Roland Garros/Paris, France/Clay, Outdoor/2020",
            "video": "some-video-link"
        }
    }


# --- Tests for Helper Functions ---

def test_parse_player_info():
    """Tests the player info parser with various formats."""
    assert _parse_player_info("Player, Name (Q)", "USA #123") == {"name": "Player, Name", "country": "USA",
                                                                  "ranking": 123}
    assert _parse_player_info("Another Player (WC)", "GER") == {"name": "Another Player", "country": "GER",
                                                                "ranking": None}
    assert _parse_player_info("Simple Player", "") == {"name": "Simple Player", "country": None, "ranking": None}


def test_parse_round_info():
    """Tests the round info parser."""
    assert _parse_round_info("Final-USD 5000-100") == {"round_name": "Final", "prize": "USD 5000", "points": "100"}
    assert _parse_round_info("First Round") == {"round_name": "First Round", "prize": None, "points": 0}


def test_parse_h2h_string():
    """Tests the H2H string parser."""
    h2h_string = "193/105/5:7 4:6 0:6/2/128/Roland Garros/Paris, France/Clay, Outdoor/2020"
    result = _parse_h2h_string(h2h_string)
    assert len(result) == 1
    assert result[0]["event"] == "Roland Garros"
    assert result[0]["year"] == "2020"
    assert _parse_h2h_string("") == []


def test_parse_stats_string():
    """Tests the main statistics string parser."""
    stats_string = "14:39/3,1,6,,2,3,,,8,5/,,3,3,3,,,,1,1"
    result = _parse_stats_string(stats_string)
    assert len(result) == 2
    service_group = result[0]["groups"][0]
    aces = next(s for s in service_group["statisticsItems"] if s["name"] == "Aces")
    assert aces["homeValue"] == 1
    assert aces["awayValue"] == 3
    assert _parse_stats_string("") == []


# --- Tests for the Main Transformer Function ---

def test_transform_match_data_fully(sample_raw_data):
    """
    Tests that the entire transformation process works and produces the correct structure.
    """
    formatted = transform_match_data_to_client_format(sample_raw_data)

    # Top level keys
    assert "tournament" in formatted
    assert "round" in formatted
    assert "players" in formatted
    assert "score" in formatted
    assert "matchInfo" in formatted
    assert "statistics" in formatted
    assert "h2h" in formatted

    # Deep checks
    assert formatted["players"][0]["name"] == "Vukic, Aleksandar"
    assert formatted["score"]["status"] == "LIVE"
    assert len(formatted["h2h"]) == 1
    assert formatted["h2h"][0]["event"] == "Roland Garros"
    assert len(formatted["statistics"]) > 0


def test_transform_with_missing_data():
    """Tests that the transformer handles missing/empty raw data gracefully."""
    raw_data = {"match": {"id": "123", "player1": "Player A", "player2": "Player B"}}

    formatted = transform_match_data_to_client_format(raw_data)

    assert formatted["tournament"] is None
    assert formatted["players"][0]["ranking"] is None
    assert formatted["statistics"] == []
    assert formatted["h2h"] == []