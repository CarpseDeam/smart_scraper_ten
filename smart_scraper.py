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

    def _get_all_intercepted_xml_bodies(self, timeout: int = 10) -> List[str]:
        """Gets the decoded bodies of ALL captured XML responses."""
        end_time = time.monotonic() + timeout
        bodies = []

        # Give the page a few seconds to make all its calls
        time.sleep(5)

        get_all_script = """
            const responses = window.interceptedResponses || {};
            const bodies = Object.values(responses);
            window.interceptedResponses = {}; // Clear after reading
            return bodies;
        """
        try:
            raw_bodies = self.driver.execute_script(get_all_script)
            for body in raw_bodies:
                decoded_body = self.driver.execute_script("return janko(arguments[0]);", body)
                if decoded_body:
                    bodies.append(decoded_body)
            logging.info(f"INTERCEPT: Successfully retrieved and decoded {len(bodies)} XML feeds.")
            return bodies
        except WebDriverException as e:
            logging.error(f"Could not execute script to get all XML bodies. Error: {e}")
            return []

    def _clear_captured_responses(self):
        if self.driver:
            try:
                self.driver.execute_script("window.interceptedResponses = {};")
                logging.debug("Cleared in-browser interception cache.")
            except WebDriverException as e:
                logging.warning(f"Could not clear browser cache, browser may have been closed. Error: {e}")

    def get_live_matches_summary(self) -> tuple[bool, List[Dict[str, Any]]]:
        """
        Loads the livescore page, dynamically discovers ALL XML data feeds,
        consolidates them, and parses them into a single list of matches.
        """
        if self.driver is None:
            return False, []
        try:
            self._clear_captured_responses()
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))

            # --- Step 1: Discover and retrieve all available XML feeds ---
            all_xml_bodies = self._get_all_intercepted_xml_bodies(timeout=15)

            if not all_xml_bodies:
                logging.warning("DISCOVERY: No XML data feeds were intercepted. The page might be empty or changed.")
                return True, []

            # --- Step 2: Consolidate all matches from all feeds ---
            all_parsed_matches = []
            parser = ET.XMLParser(recover=True, encoding='utf-8')

            for i, xml_body in enumerate(all_xml_bodies):
                root = ET.fromstring(xml_body.encode('utf-8'), parser=parser)
                if root is None:
                    continue

                matches_in_feed = 0
                for match_element in root.xpath('//match'):
                    match_data = self._xml_to_dict(match_element)
                    if 'id' in match_data and 'tournament_name' in match_data:
                        all_parsed_matches.append(match_data)
                        matches_in_feed += 1
                logging.info(f"CONSOLIDATE: Parsed {matches_in_feed} matches from feed #{i + 1}.")

            # --- Step 3: Deduplicate and Finalize ---
            # It's possible different feeds could contain the same match.
            final_matches_map = {match['id']: match for match in all_parsed_matches}
            final_match_list = list(final_matches_map.values())

            logging.info(
                f"FINALIZED: A total of {len(final_match_list)} unique matches were parsed from all discovered feeds.")

            if not final_match_list:
                logging.warning("Scraper parsed 0 unique matches from all feeds. The site may be empty.")

            return True, final_match_list

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

            # We only need the one specific match xml here
            main_xml_str = self._get_all_intercepted_xml_bodies(timeout=10)
            if not main_xml_str:
                logging.error(f"Failed to get main match data for {match_id} via interception.")
                return {}

            parser = ET.XMLParser(recover=True, encoding='utf-8')
            main_root = ET.fromstring(main_xml_str[0].encode('utf-8'), parser=parser)
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