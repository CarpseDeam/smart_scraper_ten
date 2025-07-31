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


def _to_int_score(value):
    """Safely converts a score value to an integer, handling tie-breaks."""
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return 0


def _parse_point_by_point(pbp_html_data: list) -> list:
    """Parses the point-by-point data that was scraped from the page's HTML."""
    if not pbp_html_data:
        return []

    client_pbp_data = []
    for game_block in pbp_html_data:
        client_pbp_data.append({
            "game": game_block.get("game_header", ""),
            "point_progression_log": game_block.get("points_log", [])
        })
    return client_pbp_data


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
    if not h2h_str: return []
    meetings = []
    for part in h2h_str.split('#'):
        fields = part.split('/')
        if len(fields) < 9: continue
        meetings.append({
            "year": _safe_get_from_list(fields, 8),
            "event": _safe_get_from_list(fields, 5),
            "surface": _safe_get_from_list(fields, 7),
            "score": _safe_get_from_list(fields, 2),
        })
    return meetings


def _parse_stats_string(stats_str: str) -> list:
    """Parses the dense stats string into the client's detailed format."""
    if not stats_str or '/' not in stats_str: return []
    STAT_MAP = {
        1: "Aces", 2: "Double Faults", 3: "1st Serve", 4: "1st Serve Points Won",
        5: "2nd Serve Points Won", 6: "Break Points Saved", 7: "Service Games Played",
        8: "1st Serve Return Points Won", 9: "2nd Serve Return Points Won",
        10: "Break Points Converted", 11: "Return Games Played"
    }
    try:
        _, p1_stats_str, p2_stats_str = stats_str.split('/')
        p1_vals = p1_stats_str.split(',')
        p2_vals = p2_stats_str.split(',')
    except ValueError:
        return []

    service_stats, return_stats = [], []
    for i in range(1, 12):
        stat_name = STAT_MAP.get(i)
        if not stat_name: continue
        if i == 1:
            p1_val, p2_val = _safe_get_from_list(p1_vals, 1, "0"), _safe_get_from_list(p1_vals, 0, "0")
        else:
            p1_val, p2_val = _safe_get_from_list(p1_vals, i, "0"), _safe_get_from_list(p2_vals, i, "0")
        stat_item = {"name": stat_name, "home": p1_val, "away": p2_val}
        if "Serve" in stat_name or "Aces" in stat_name or "Double" in stat_name or "Games Played" in stat_name:
            service_stats.append(stat_item)
        else:
            return_stats.append(stat_item)
    return [
        {"groupName": "Service", "statisticsItems": service_stats},
        {"groupName": "Return", "statisticsItems": return_stats}
    ]


# FIX: Added 'match_id' as an argument to the function.
def transform_match_data_to_client_format(raw_data: dict, match_id: str) -> dict:
    if "match" not in raw_data:
        logging.warning("transform_match_data called with invalid data format.")
        return {}

    match_info = raw_data["match"]
    pbp_info = raw_data.get("point_by_point_html", [])

    p1_info = _parse_player_info(_safe_get_from_dict(match_info, "player1", ""),
                                 _safe_get_from_dict(match_info, "country1", ""))
    p2_info = _parse_player_info(_safe_get_from_dict(match_info, "player2", ""),
                                 _safe_get_from_dict(match_info, "country2", ""))

    return {
        # FIX: Added the match_url field to satisfy the database's unique index.
        "match_url": f"https://tenipo.com/match/-/{match_id}",
        "tournament": _safe_get_from_dict(match_info, "tournament_name"),
        "round": _parse_round_info(_safe_get_from_dict(match_info, "round", "")).get("round_name"),
        "timePolled": datetime.now(timezone.utc).isoformat(),
        "players": [p1_info, p2_info],
        "score": {
            "sets": [
                {"p1": _to_int_score(_safe_get_from_dict(match_info, "set11")),
                 "p2": _to_int_score(_safe_get_from_dict(match_info, "set12"))},
                {"p1": _to_int_score(_safe_get_from_dict(match_info, "set21")),
                 "p2": _to_int_score(_safe_get_from_dict(match_info, "set22"))},
                {"p1": _to_int_score(_safe_get_from_dict(match_info, "set31")),
                 "p2": _to_int_score(_safe_get_from_dict(match_info, "set32"))},
                {"p1": _to_int_score(_safe_get_from_dict(match_info, "set41")),
                 "p2": _to_int_score(_safe_get_from_dict(match_info, "set42"))},
                {"p1": _to_int_score(_safe_get_from_dict(match_info, "set51")),
                 "p2": _to_int_score(_safe_get_from_dict(match_info, "set52"))},
            ],
            "currentGame": {"p1": _safe_get_from_dict(match_info, "game1"),
                            "p2": _safe_get_from_dict(match_info, "game2")},
            "status": "LIVE" if _safe_get_from_dict(match_info, "winner") == "0" else "COMPLETED"
        },
        "matchInfo": {
            "court": _safe_get_from_dict(match_info, "court_name"),
            "started": datetime.fromtimestamp(_to_int_score(_safe_get_from_dict(match_info, "starttime")),
                                              tz=timezone.utc).isoformat() if _safe_get_from_dict(match_info,
                                                                                                  "starttime") else None,
        },
        "statistics": _parse_stats_string(_safe_get_from_dict(match_info, "stats", "")),
        "pointByPoint": _parse_point_by_point(pbp_info),
        "h2h": _parse_h2h_string(_safe_get_from_dict(match_info, "h2h", "")),
    }