# smart_scraper.py
import logging
import os
import shutil
import uuid
import json
import time
from lxml import etree as ET
from typing import List, Dict, Any

import config
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException, TimeoutException, NoSuchElementException, \
    StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class TenipoScraper:
    def __init__(self, settings: config.Settings):
        self.settings = settings
        self.driver: webdriver.Chrome | None = None
        self.profile_path: str | None = None

    def start_driver(self):
        if self.driver is None:
            logging.info("Initializing new Selenium driver with JS interception...")
            self.driver = self._setup_driver()

            script_source = """
                window.interceptedResponses = window.interceptedResponses || {};
                const originalSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.send = function(body) {
                    this.addEventListener('load', function() {
                        try {
                            if (this.responseURL && this.responseURL.includes('.xml')) {
                                window.interceptedResponses[this.responseURL] = this.responseText;
                            }
                        } catch (e) {
                            console.error('Interception script error:', e);
                        }
                    });
                    originalSend.call(this, body);
                };
            """
            self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": script_source})

    def _setup_driver(self) -> webdriver.Chrome:
        chrome_options = Options()
        self.profile_path = os.path.join("/tmp", f"selenium-profile-{uuid.uuid4()}")
        chrome_options.add_argument(f"--user-data-dir={self.profile_path}")

        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--remote-debugging-port=0")
        chrome_options.add_argument(f"user-agent={self.settings.USER_AGENT}")

        try:
            driver = webdriver.Chrome(options=chrome_options)
            driver.set_page_load_timeout(30)
            return driver
        except WebDriverException as e:
            logging.critical(f"Failed to set up Selenium WebDriver: {e}")
            raise

    def close(self):
        if self.driver:
            self.driver.quit()
            self.driver = None
        if self.profile_path and os.path.exists(self.profile_path):
            try:
                shutil.rmtree(self.profile_path)
            except OSError as e:
                logging.error(f"Error cleaning up profile {self.profile_path}: {e}")

    def _get_intercepted_xml(self, url_pattern: str, timeout: int = 15) -> str | None:
        end_time = time.monotonic() + timeout
        while time.monotonic() < end_time:
            script = f"""
                const url = Object.keys(window.interceptedResponses || {{}}).find(k => k.includes('{url_pattern}'));
                if (url) {{
                    const body = window.interceptedResponses[url];
                    delete window.interceptedResponses[url];
                    return body;
                }}
                return null;
            """
            try:
                body = self.driver.execute_script(script)
                if body:
                    decoded_body = self.driver.execute_script("return janko(arguments[0]);", body)
                    logging.info(f"INTERCEPT: Successfully retrieved and decoded data for '{url_pattern}'.")
                    return decoded_body
            except WebDriverException as e:
                logging.warning(f"Could not execute script, browser may be navigating. Retrying... Error: {e}")

            time.sleep(0.25)

        logging.error(f"INTERCEPT TIMEOUT: Did not intercept a response for '{url_pattern}' after {timeout} seconds.")
        return None

    def _clear_captured_responses(self):
        if self.driver:
            try:
                self.driver.execute_script("window.interceptedResponses = {};")
                logging.debug("Cleared in-browser interception cache.")
            except WebDriverException as e:
                logging.warning(f"Could not clear browser cache, browser may have been closed. Error: {e}")

    def _get_tournament_name_from_element(self, element: ET.Element) -> str:
        """Helper to consistently parse a tournament name from an <event> element."""
        if element is None or element.tag != 'event':
            return ""
        name_parts = [element.get("name", ""), element.get("tournament_name", ""), element.get("category", "")]
        return " ".join(part for part in name_parts if part).strip()

    def get_live_matches_summary(self) -> tuple[bool, List[Dict[str, Any]]]:
        if self.driver is None:
            return False, []
        try:
            self._clear_captured_responses()
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))

            decoded_xml_string = self._get_intercepted_xml("change2.xml", timeout=20)
            if not decoded_xml_string:
                logging.warning("Could not find change2.xml via interception. Returning failure status.")
                return False, []

            # --- DIAGNOSTIC STEP: DUMP THE RAW XML TO LOGS ---
            logging.info("--- RAW change2.xml CONTENT START ---")
            logging.info(decoded_xml_string.strip())
            logging.info("--- RAW change2.xml CONTENT END ---")
            # --- END DIAGNOSTIC STEP ---

            parser = ET.XMLParser(recover=True, encoding='utf-8')
            root = ET.fromstring(decoded_xml_string.encode('utf-8'), parser=parser)
            if root is None:
                logging.warning("Parsed XML root is None. Returning failure status.")
                return False, []

            # --- Resilient "Detective" Parser ---

            # 1. Build a map of all known tournament IDs first.
            tournaments_by_id = {
                event.get("id"): self._get_tournament_name_from_element(event)
                for event in root.xpath('//event[@id]')
                if self._get_tournament_name_from_element(event)
            }

            all_parsed_matches = []
            # 2. Find every match element in the document, regardless of location.
            for match_element in root.xpath('//match'):
                match_data = self._xml_to_dict(match_element)
                tournament_name = "Unknown"

                # Method A: Check if the direct parent is an event.
                parent = match_element.getparent()
                if parent is not None and parent.tag == 'event':
                    tournament_name = self._get_tournament_name_from_element(parent)

                # Method B: If not, check if the match has a linkable event_id.
                if tournament_name == "Unknown" and match_data.get("event_id") in tournaments_by_id:
                    tournament_name = tournaments_by_id[match_data["event_id"]]

                # Method C: If not, check the preceding sibling.
                if tournament_name == "Unknown":
                    prev_sibling = match_element.getprevious()
                    if prev_sibling is not None and prev_sibling.tag == 'event':
                        tournament_name = self._get_tournament_name_from_element(prev_sibling)

                # Method D: As a final fallback, find the nearest preceding event in the whole document.
                if tournament_name == "Unknown":
                    # This XPath finds the very first <event> tag that appears before the current <match> tag.
                    preceding_events = match_element.xpath('./preceding::event')
                    if preceding_events:
                        tournament_name = self._get_tournament_name_from_element(preceding_events[-1])

                match_data['tournament_name'] = tournament_name
                all_parsed_matches.append(match_data)

            logging.info(f"Parsed a total of {len(all_parsed_matches)} matches from summary.")

            if not all_parsed_matches:
                logging.warning(
                    "SANITY CHECK FAILED: Scraper parsed 0 matches from summary. Forcing a failure status to protect DB.")
                return False, []

            return True, all_parsed_matches

        except Exception as e:
            logging.error(f"Error in get_live_matches_summary: {e}", exc_info=True)
            return False, []

    def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        if self.driver is None: return {}
        match_page_url = f"https://tenipo.com/match/-/{match_id}"
        logging.info(f"FETCHING data for match ID: {match_id}")
        try:
            self._clear_captured_responses()
            self.driver.get(match_page_url)

            main_xml_str = self._get_intercepted_xml(f"match{match_id}.xml", timeout=10)
            if not main_xml_str:
                logging.error(f"Failed to get main match data for {match_id} via interception.")
                return {}

            parser = ET.XMLParser(recover=True, encoding='utf-8')
            main_root = ET.fromstring(main_xml_str.encode('utf-8'), parser=parser)
            combined_data = {"match": self._xml_to_dict(main_root)}

            pbp_html_data = self._scrape_html_pbp()
            combined_data['point_by_point_html'] = pbp_html_data
            return combined_data
        except Exception as e:
            logging.error(f"FATAL error in fetch_match_data for ID {match_id}: {e}", exc_info=True)
            return {}

    def _xml_to_dict(self, element: ET.Element) -> dict:
        if element is None: return {}
        result = {}
        if element.attrib: result.update(element.attrib)
        if element.text and element.text.strip(): result['#text'] = element.text.strip()
        for child in element:
            child_data = self._xml_to_dict(child)
            if child.tag in result:
                if not isinstance(result[child.tag], list): result[child.tag] = [result[child.tag]]
                result[child.tag].append(child_data)
            else:
                result[child.tag] = child_data
        return result

    def _scrape_html_pbp(self) -> List[Dict[str, Any]]:
        if self.driver is None:
            return []

        try:
            pbp_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.ID, "buttonhistoryall"))
            )
            self.driver.execute_script("arguments[0].click();", pbp_button)
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "ohlavicka1"))
            )

            for attempt in range(3):
                pbp_data = []
                try:
                    game_headers = self.driver.find_elements(By.CLASS_NAME, "ohlavicka1")
                    game_point_blocks = self.driver.find_elements(By.CLASS_NAME, "sethistory")

                    for header, block in zip(game_headers, game_point_blocks):
                        try:
                            score = header.find_element(By.CLASS_NAME, "ohlavicka3").text.strip()
                            points_elements = block.find_elements(By.CLASS_NAME, "pointlogg")
                            points = [p.text.strip().replace('\n', ' ') for p in points_elements]
                            pbp_data.append({"game_header": score, "points_log": points})
                        except NoSuchElementException:
                            logging.debug("Skipping a PBP block that was missing expected elements.")
                            continue

                    return pbp_data

                except StaleElementReferenceException:
                    logging.warning(
                        f"PBP scrape attempt {attempt + 1}/3 failed due to StaleElementReferenceException. Retrying...")
                    if attempt < 2:
                        time.sleep(0.5)
                    else:
                        logging.error("PBP scraping failed after 3 retries due to persistent staleness.")
                        return []
        except TimeoutException:
            logging.info("No point-by-point data available or button not found for this match.")
            return []
        except Exception as e:
            logging.error(f"An unexpected error occurred during PBP scraping: {e}", exc_info=True)
            return []

        return []

    def investigate_data_sources(self, match_id: str):
        """
        A debugging method to find new data sources for a given match.
        """
        if not self.driver:
            logging.error("Cannot investigate, driver not started.")
            return []

        # Navigate to the page
        match_page_url = f"https://tenipo.com/match/-/{match_id}"
        self.driver.get(match_page_url)

        time.sleep(10)  # Wait for requests to be made

        script = "return Object.keys(window.interceptedResponses || {});"
        urls = self.driver.execute_script(script)

        logging.info(f"--- Investigation for Match ID {match_id} ---")
        logging.info(f"Found {len(urls)} intercepted URLs:")
        for url in urls:
            logging.info(f"  - {url}")
        logging.info("--- End of Investigation ---")

        return urls