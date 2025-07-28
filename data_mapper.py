import logging
import re
from datetime import datetime, timezone


# --- Helper Functions ---

def _safe_get_from_dict(data, key, default=None):
    """Safely get a value from a dictionary; return default if key is missing or value is empty."""
    val = data.get(key)
    return val if val is not None and val != "" else default


def _safe_get_from_list(data_list, index, default=None):
    """Safely get a value from a list by index; return default if index is out of bounds or value is empty."""
    try:
        val = data_list[index]
        return val if val is not None and val != "" else default
    except IndexError:
        return default


def _parse_player_info(player_str, country_str):
    """Parses player and country strings into a structured dict."""
    name = player_str.replace(" (Q)", "").replace(" (WC)", "").strip()
    country_code = _safe_get_from_list(country_str.split(" "), 0)
    ranking_match = re.search(r'#(\d+)', country_str)
    ranking = int(ranking_match.group(1)) if ranking_match else None
    return {"name": name, "country": country_code, "ranking": ranking}


def _parse_round_info(round_str):
    """Parses the round string for prize money and points."""
    parts = round_str.split('-')
    return {
        "round_name": _safe_get_from_list(parts, 0),
        "prize": _safe_get_from_list(parts, 1),
        "points": _safe_get_from_list(parts, 2, default=0),
    }


def _parse_h2h_string(h2h_str: str) -> list:
    """Parses the dense H2H string into a list of previous meetings."""
    if not h2h_str:
        return []

    meetings = []
    # Meetings are separated by '#'
    for part in h2h_str.split('#'):
        # Format seems to be: "p1_id/p2_id/score/winner/tour_id/event/site/surface/year"
        fields = part.split('/')
        if len(fields) < 9:
            continue
        meetings.append({
            "year": _safe_get_from_list(fields, 8),
            "event": _safe_get_from_list(fields, 5),
            "round": None,  # Not available in this raw string
            "surface": _safe_get_from_list(fields, 7),
            "score": _safe_get_from_list(fields, 2),
            "rankings": f"#{_safe_get_from_list(fields, 0)} / #{_safe_get_from_list(fields, 1)}",  # Example format
            "series": None  # Not available
        })
    return meetings


def _parse_stats_string(stats_str: str) -> list:
    """Parses the dense stats string into the client's detailed format."""
    if not stats_str or '/' not in stats_str:
        return []

    # Mapping from index in the raw string to the statistic's name
    STAT_MAP = {
        1: "Aces", 2: "Double Faults", 3: "1st Serve", 4: "1st Serve Points Won",
        5: "2nd Serve Points Won", 6: "Break Points Saved", 7: "Service Games Played",
        8: "1st Serve Return Points Won", 9: "2nd Serve Return Points Won",
        10: "Break Points Converted", 11: "Return Games Played"
    }

    try:
        time_part, p1_stats_str, p2_stats_str = stats_str.split('/')
        p1_vals = p1_stats_str.split(',')
        p2_vals = p2_stats_str.split(',')
    except ValueError:
        return []

    service_stats = []
    return_stats = []

    for i in range(1, 12):
        stat_name = STAT_MAP.get(i)
        if not stat_name: continue

        if i == 1:  # Special case for "Aces" due to quirky data format
            p1_val = _safe_get_from_list(p1_vals, 1, "0") or "0"
            p2_val = _safe_get_from_list(p1_vals, 0, "0") or "0"
        else:
            p1_val = _safe_get_from_list(p1_vals, i, "0") or "0"
            p2_val = _safe_get_from_list(p2_vals, i, "0") or "0"

        stat_item = {
            "name": stat_name,
            "home": p1_val, "away": p2_val,
            "compareCode": 3, "statisticsType": "positive", "valueType": "event",
            "homeValue": int(p1_val.replace('%', '')) if p1_val.replace('%', '').isdigit() else 0,
            "awayValue": int(p2_val.replace('%', '')) if p2_val.replace('%', '').isdigit() else 0,
            "renderType": 1, "key": stat_name.lower().replace(" ", "")
        }
        if "Serve" in stat_name or "Aces" in stat_name or "Double" in stat_name:
            service_stats.append(stat_item)
        else:
            return_stats.append(stat_item)

    return [
        {"period": "ALL", "groups": [{"groupName": "Service", "statisticsItems": service_stats}]},
        {"period": "ALL", "groups": [{"groupName": "Return", "statisticsItems": return_stats}]}
    ]


# --- Main Transformer ---

def transform_match_data_to_client_format(raw_data: dict) -> dict:
    """
    Transforms the raw scraper output into the client's desired JSON format.
    """
    if "match" not in raw_data:
        logging.warning("transform_match_data called with invalid data format.")
        return {}

    match_info = raw_data["match"]
    round_details = _parse_round_info(_safe_get_from_dict(match_info, "round", ""))

    p1_info = _parse_player_info(_safe_get_from_dict(match_info, "player1", ""),
                                 _safe_get_from_dict(match_info, "country1", ""))
    p2_info = _parse_player_info(_safe_get_from_dict(match_info, "player2", ""),
                                 _safe_get_from_dict(match_info, "country2", ""))

    client_output = {
        "tournament": _safe_get_from_dict(match_info, "tournament_name"),
        "round": round_details.get("round_name"),
        "timePolled": datetime.now(timezone.utc).isoformat(),
        "players": [p1_info, p2_info],
        "score": {
            "sets": [
                {"p1": int(_safe_get_from_dict(match_info, "set11", 0)),
                 "p2": int(_safe_get_from_dict(match_info, "set12", 0))},
                {"p1": int(_safe_get_from_dict(match_info, "set21", 0)),
                 "p2": int(_safe_get_from_dict(match_info, "set22", 0))},
                {"p1": int(_safe_get_from_dict(match_info, "set31", 0)),
                 "p2": int(_safe_get_from_dict(match_info, "set32", 0))},
                {"p1": int(_safe_get_from_dict(match_info, "set41", 0)),
                 "p2": int(_safe_get_from_dict(match_info, "set42", 0))},
                {"p1": int(_safe_get_from_dict(match_info, "set51", 0)),
                 "p2": int(_safe_get_from_dict(match_info, "set52", 0))},
            ],
            "currentGame": {
                "p1": _safe_get_from_dict(match_info, "game1"),
                "p2": _safe_get_from_dict(match_info, "game2")
            },
            "status": "LIVE" if _safe_get_from_dict(match_info, "winner") == "0" else "COMPLETED"
        },
        "matchInfo": {
            "court": _safe_get_from_dict(match_info, "court_name"),
            "started": datetime.fromtimestamp(int(_safe_get_from_dict(match_info, "starttime", 0)),
                                              tz=timezone.utc).isoformat(),
            "completed": None,
            "winnerPrize": round_details.get("prize"),
            "winnerPoints": round_details.get("points"),
            "videoLink": _safe_get_from_dict(match_info, "video")
        },
        "pointByPoint": [],
        "statistics": _parse_stats_string(_safe_get_from_dict(match_info, "stats", "")),
        "h2h": _parse_h2h_string(_safe_get_from_dict(match_info, "h2h", "")),
        "matchUrl": f"https://tenipo.com/match/placeholder-match-url/{_safe_get_from_dict(match_info, 'id')}"
    }

    return client_output