# data_mapper.py
import re
from datetime import datetime, timezone
from typing import Dict, Any, List


# --- Helper Functions ---

def _safe_get_from_dict(data, key, default=None):
    """Safely get a value from a dictionary; return default if key is missing or value is empty."""
    val = data.get(key)
    return val if val is not None and val != "" else default


def _get_value_with_fallbacks(data: Dict, keys: List[str], default=None):
    """
    Safely get a value from a dictionary by trying a list of possible keys in order.
    Returns the first non-empty value found.
    """
    for key in keys:
        if key in data:
            val = data.get(key)
            if val is not None and val != "":
                return val
    return default


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


def _parse_player_info(player_str, country_str):
    """Parses player and country strings into a structured dict."""
    name = player_str.replace(" (Q)", "").replace(" (WC)", "").strip()
    country_code = _safe_get_from_list(country_str.split(" "), 0)
    ranking_match = re.search(r'#(\d+)', country_str)
    ranking = int(ranking_match.group(1)) if ranking_match else None
    return {"name": name, "country": country_code, "ranking": ranking}


def _parse_point_by_point(pbp_html_data: list) -> list:
    """Parses the point-by-point data that was scraped from the page's HTML."""
    if not pbp_html_data: return []
    client_pbp_data = []
    for game_block in pbp_html_data:
        client_pbp_data.append({
            "game": game_block.get("game_header", ""),
            "point_progression_log": game_block.get("points_log", [])
        })
    return client_pbp_data


def _parse_h2h_string(h2h_str: Any) -> list:
    """Parses the dense H2H string into a list of previous meetings."""
    if not isinstance(h2h_str, str) or not h2h_str:
        return []
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


def _parse_stats_string(stats_str: Any) -> list:
    """Parses the dense stats string into the client's detailed format."""
    if not isinstance(stats_str, str) or '/' not in stats_str: return []
    STAT_MAP = {
        1: "Aces", 2: "Double Faults", 3: "1st Serve", 4: "1st Serve Points Won",
        5: "2nd Serve Points Won", 6: "Break Points Saved", 7: "Service Games Played",
        8: "1st Serve Return Points Won", 9: "2nd Serve Return Points Won",
        10: "Break Points Converted", 11: "Return Games Played"
    }
    try:
        _, p1_stats_str, p2_stats_str = stats_str.split('/')
        p1_vals, p2_vals = p1_stats_str.split(','), p2_stats_str.split(',')
    except ValueError:
        return []

    service_stats, return_stats = [], []
    for i in range(1, 12):
        stat_name = STAT_MAP.get(i)
        if not stat_name: continue
        p1_val = _safe_get_from_list(p1_vals, i, "0") if i != 1 else _safe_get_from_list(p1_vals, 1, "0")
        p2_val = _safe_get_from_list(p2_vals, i, "0") if i != 1 else _safe_get_from_list(p1_vals, 0, "0")
        stat_item = {"name": stat_name, "home": p1_val, "away": p2_val}
        if "Serve" in stat_name or "Aces" in stat_name or "Double" in stat_name:
            service_stats.append(stat_item)
        else:
            return_stats.append(stat_item)
    return [
        {"groupName": "Service", "statisticsItems": service_stats},
        {"groupName": "Return", "statisticsItems": return_stats}
    ]


def transform_summary_only_to_client_format(summary_data: dict) -> dict:
    """
    🏎️ FAST LANE: Transforms ONLY summary data for lightning-fast live score updates.
    This creates the core match structure with live scores but leaves detailed fields empty.
    The slow lane will enrich these later.
    """
    match_id = summary_data.get('id')
    if not match_id:
        return {}

    # Parse player info from summary
    p1_info = _parse_player_info(summary_data.get("player1", ""), "")
    p2_info = _parse_player_info(summary_data.get("player2", ""), "")

    # Get live score data from summary
    live_score = summary_data.get("live_score_data", {})
    sets_from_summary = live_score.get("sets", [])
    game_from_summary = live_score.get("currentGame", {})

    # Build sets list from summary data
    sets_list = []
    for i, s in enumerate(sets_from_summary):
        set_num = i + 1
        p1_score = _to_int_score(s.get("p1"))
        p2_score = _to_int_score(s.get("p2"))

        set_data = {
            "p1": p1_score,
            "p2": p2_score,
            "p1_tiebreak": None,  # Will be enriched later if needed
            "p2_tiebreak": None
        }
        sets_list.append(set_data)

    # Handle current game vs tiebreak
    current_game_score = {
        "p1": game_from_summary.get("p1"),
        "p2": game_from_summary.get("p2")
    }
    current_tiebreak_score = None

    # Check if we're in a tiebreak (6-6 in last set)
    last_set = sets_list[-1] if sets_list else {}
    if last_set.get("p1") == 6 and last_set.get("p2") == 6:
        current_tiebreak_score = current_game_score
        current_game_score = None

    return {
        "_id": match_id,
        "match_url": f"https://tenipo.com/match/-/{match_id}",
        "tournament": summary_data.get("tournament_name", "N/A"),
        "round": None,  # Will be enriched by slow lane
        "timePolled": datetime.now(timezone.utc).isoformat(),
        "players": [p1_info, p2_info],
        "score": {
            "sets": sets_list,
            "currentGame": current_game_score,
            "currentTiebreak": current_tiebreak_score,
            "status": "LIVE"
        },
        "matchInfo": {
            "court": None,  # Will be enriched by slow lane
            "started": None,  # Will be enriched by slow lane
        },
        # These will be populated by the slow lane
        "statistics": [],
        "pointByPoint": [],
        "h2h": [],
        # Track data completeness
        "hasDetailedData": False,
        "detailedDataUpdated": None
    }


def transform_match_data_to_client_format(raw_data: dict, summary_data: dict) -> dict:
    """
    🐌 SLOW LANE: Full transformation including detailed data.
    This is the original function, kept for backward compatibility and slow lane enrichment.
    """
    match_id = summary_data.get('id')
    match_details_xml = raw_data.get("match", {})
    pbp_info = raw_data.get("point_by_point_html", [])
    stats_from_html = raw_data.get("statistics_html", [])

    p1_info = _parse_player_info(summary_data.get("player1", ""), "")
    p2_info = _parse_player_info(summary_data.get("player2", ""), "")

    live_score = summary_data.get("live_score_data", {})
    sets_from_summary = live_score.get("sets", [])
    game_from_summary = live_score.get("currentGame", {})

    sets_list = []
    for i, s in enumerate(sets_from_summary):
        set_num = i + 1
        p1_score = _to_int_score(s.get("p1"))
        p2_score = _to_int_score(s.get("p2"))

        set_data = {"p1": p1_score, "p2": p2_score}

        # TIEBREAK ENRICHMENT from detailed XML
        if abs(p1_score - p2_score) == 1 and (p1_score == 7 or p2_score == 7):
            p1_tb_raw = _get_value_with_fallbacks(match_details_xml, [f"s{set_num}tb1", f"set{set_num}tb1"])
            p2_tb_raw = _get_value_with_fallbacks(match_details_xml, [f"s{set_num}tb2", f"set{set_num}tb2"])
            set_data["p1_tiebreak"] = _to_int_score(p1_tb_raw)
            set_data["p2_tiebreak"] = _to_int_score(p2_tb_raw)
        else:
            set_data["p1_tiebreak"] = None
            set_data["p2_tiebreak"] = None

        sets_list.append(set_data)

    status = "LIVE"

    current_game_score = {
        "p1": game_from_summary.get("p1"),
        "p2": game_from_summary.get("p2")
    }
    current_tiebreak_score = None

    last_set = sets_list[-1] if sets_list else {}
    if last_set.get("p1") == 6 and last_set.get("p2") == 6:
        current_tiebreak_score = current_game_score
        current_game_score = None

    h2h = _get_value_with_fallbacks(match_details_xml, ["h2h"], "")
    stats_from_xml = _parse_stats_string(_get_value_with_fallbacks(match_details_xml, ["stats", "statistics"], ""))
    final_statistics = stats_from_html if stats_from_html else stats_from_xml

    return {
        "_id": match_id,
        "match_url": f"https://tenipo.com/match/-/{match_id}",
        "tournament": summary_data.get("tournament_name", "N/A"),
        "round": _safe_get_from_dict(match_details_xml, "round"),
        "timePolled": datetime.now(timezone.utc).isoformat(),
        "players": [p1_info, p2_info],
        "score": {
            "sets": sets_list,
            "currentGame": current_game_score,
            "currentTiebreak": current_tiebreak_score,
            "status": status
        },
        "matchInfo": {
            "court": _safe_get_from_dict(match_details_xml, "court_name"),
            "started": datetime.fromtimestamp(_to_int_score(_safe_get_from_dict(match_details_xml, "starttime")),
                                              tz=timezone.utc).isoformat() if _safe_get_from_dict(match_details_xml,
                                                                                                  "starttime") else None,
        },
        "statistics": final_statistics,
        "pointByPoint": _parse_point_by_point(pbp_info),
        "h2h": _parse_h2h_string(h2h),
        "hasDetailedData": True,
        "detailedDataUpdated": datetime.now(timezone.utc).isoformat()
    }