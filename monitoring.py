# monitoring.py
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict

import httpx
import config


class TelegramNotifier:
    """Handles sending messages to a Telegram channel."""

    def __init__(self, settings: config.Settings):
        if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
            logging.warning("Telegram settings (TOKEN/CHAT_ID) are not configured. Notifications will be disabled.")
            self.enabled = False
            return

        self.api_url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.enabled = True
        logging.info("TelegramNotifier initialized.")

    async def send_alert(self, message: str):
        """Sends a formatted message to the pre-configured Telegram chat."""
        if not self.enabled:
            logging.warning("Tried to send Telegram alert, but notifier is disabled.")
            return

        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(self.api_url, json=payload, timeout=10)
                response.raise_for_status()
                logging.info(f"Successfully sent Telegram alert to chat ID {self.chat_id}.")
        except httpx.HTTPStatusError as e:
            logging.error(f"Telegram API returned an error: {e.response.status_code} - {e.response.text}")
        except httpx.RequestError as e:
            logging.error(f"Failed to send Telegram alert due to a network error: {e}")
        except Exception as e:
            logging.error(f"An unexpected error occurred in TelegramNotifier: {e}")


class StallMonitor:
    """Tracks live matches and detects when their data goes stale."""

    def __init__(self, notifier: TelegramNotifier, settings: config.Settings):
        self.notifier = notifier
        self.stall_duration = timedelta(seconds=settings.STALL_MONITOR_SECONDS)
        self._match_states: Dict[str, Dict] = {}
        logging.info(f"StallMonitor initialized with a {settings.STALL_MONITOR_SECONDS}-second threshold.")

    def _create_score_hash(self, match_data: dict) -> str:
        """Creates a simple, comparable string representing the current score."""
        try:
            score = match_data.get("score", {})
            sets = score.get("sets", [])
            current_game = score.get("currentGame", {})
            sets_str = "".join([f"{s.get('p1', '0')}{s.get('p2', '0')}" for s in sets])
            game_str = f"{current_game.get('p1', '-')}{current_game.get('p2', '-')}"
            return f"{sets_str}_{game_str}"
        except Exception:
            return str(datetime.now(timezone.utc).timestamp())

    def _format_alert_message(self, match_data: dict) -> str:
        """Creates a human-readable alert message for Telegram."""
        try:
            p1_name = match_data['players'][0]['name']
            p2_name = match_data['players'][1]['name']

            score_parts = []
            for s in match_data['score']['sets']:
                if s['p1'] > 0 or s['p2'] > 0:
                    score_parts.append(f"{s['p1']}-{s['p2']}")
            game = match_data['score']['currentGame']
            game_score = f"({game.get('p1', '0')}-{game.get('p2', '0')})"

            return (
                f"🚨 **Match Stall Alert** 🚨\n\n"
                f"**Tournament:** {match_data.get('tournament', 'N/A')}\n"
                f"**Match:** {p1_name} vs {p2_name}\n"
                f"**Score:** {' '.join(score_parts)} {game_score}\n\n"
                f"*The score has not changed for over {self.stall_duration.total_seconds() / 60:.0f} minutes. "
                f"This could indicate a delay (e.g., rain, injury, etc.).*"
            )
        except (KeyError, IndexError) as e:
            logging.error(f"Could not format alert message due to missing data: {e}")
            return "🚨 **Match Stall Alert** 🚨\n\nCould not format all match details due to unexpected data."

    async def check_and_update_all(self, all_current_matches: Dict[str, Dict]):
        """Processes all current matches, detects stalls, sends alerts, and prunes old data."""
        now = datetime.now(timezone.utc)
        live_match_ids = set(all_current_matches.keys())
        alert_tasks = []

        for match_id, match_data in all_current_matches.items():
            if match_data.get("score", {}).get("status") != "LIVE":
                continue

            current_score_hash = self._create_score_hash(match_data)

            if match_id not in self._match_states:
                self._match_states[match_id] = {
                    "score_hash": current_score_hash,
                    "last_updated": now,
                    "alert_sent": False
                }
                logging.info(f"STALL_MONITOR: Now tracking new match ID: {match_id}")
                continue

            previous_state = self._match_states[match_id]
            if previous_state["score_hash"] != current_score_hash:
                previous_state["score_hash"] = current_score_hash
                previous_state["last_updated"] = now
                previous_state["alert_sent"] = False
                logging.debug(f"STALL_MONITOR: Score updated for match ID: {match_id}")
            else:
                time_since_last_update = now - previous_state["last_updated"]
                if time_since_last_update > self.stall_duration and not previous_state["alert_sent"]:
                    logging.warning(f"STALL_MONITOR: STALL DETECTED for match ID: {match_id}")
                    message = self._format_alert_message(match_data)
                    alert_tasks.append(self.notifier.send_alert(message))
                    previous_state["alert_sent"] = True

        tracked_ids = set(self._match_states.keys())
        ids_to_prune = tracked_ids - live_match_ids
        if ids_to_prune:
            for match_id in ids_to_prune:
                del self._match_states[match_id]
            logging.info(f"STALL_MONITOR: Pruned {len(ids_to_prune)} completed/old matches from tracking.")

        if alert_tasks:
            logging.info(f"STALL_MONITOR: Sending {len(alert_tasks)} stall alerts...")
            await asyncio.gather(*alert_tasks)