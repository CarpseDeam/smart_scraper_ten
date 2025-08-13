import logging
import os
import shutil
import uuid
from lxml import etree as ET
from typing import List, Dict, Any

import config
from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException, TimeoutException, NoSuchElementException
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
        """Initializes the Selenium WebDriver instance."""
        if self.driver is None:
            logging.info("Initializing new Selenium driver...")
            self.driver = self._setup_driver()
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))

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

        # --- CRITICAL FIX: Scope selenium-wire to only capture tenipo.com requests ---
        # This reduces overhead and cleans the logs immensely.
        seleniumwire_options = {
            'disable_encoding': True,
            'scope': r'.*tenipo\.com.*'
        }

        try:
            driver = webdriver.Chrome(options=chrome_options, seleniumwire_options=seleniumwire_options)
            driver.set_page_load_timeout(30)
            return driver
        except WebDriverException as e:
            logging.critical(f"Failed to set up Selenium WebDriver: {e}")
            raise

    def close(self):
        """Closes the driver and cleans up the profile directory."""
        if self.driver:
            self.driver.quit()
            self.driver = None

        if self.profile_path and os.path.exists(self.profile_path):
            try:
                shutil.rmtree(self.profile_path)
                logging.info(f"Successfully cleaned up profile: {self.profile_path}")
            except OSError as e:
                logging.error(f"Error cleaning up profile {self.profile_path}: {e}")
        self.profile_path = None

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

    def get_live_matches_summary(self) -> List[Dict[str, Any]]:
        if self.driver is None:
            logging.error("Driver is not started. Cannot get summary.")
            return []
        try:
            del self.driver.requests
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))
            request = self.driver.wait_for_request(r'/change2\.xml', timeout=25)
            payload_str = request.response.body.decode('latin-1')
            decoded_xml_string = self.driver.execute_script("return janko(arguments[0]);", payload_str)
            if not decoded_xml_string: return []
            parser = ET.XMLParser(recover=True, encoding='utf-8')
            root = ET.fromstring(decoded_xml_string.encode('utf-8'), parser=parser)
            if root is None: return []
            match_tags = root.findall("./match") or root.findall("./event")
            logging.info(f"Found {len(match_tags)} match/event tags in summary XML.")
            return [self._xml_to_dict(tag) for tag in match_tags]
        except Exception as e:
            logging.error(f"Error in get_live_matches_summary: {e}", exc_info=True)
            return []

    def _scrape_html_pbp(self) -> List[Dict[str, Any]]:
        if self.driver is None: return []
        pbp_data = []
        try:
            pbp_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.ID, "buttonhistoryall"))
            )
            self.driver.execute_script("arguments[0].click();", pbp_button)
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "ohlavicka1"))
            )
            game_headers = self.driver.find_elements(By.CLASS_NAME, "ohlavicka1")
            game_point_blocks = self.driver.find_elements(By.CLASS_NAME, "sethistory")
            for header_element, points_block_element in zip(game_headers, game_point_blocks):
                try:
                    header_score = header_element.find_element(By.CLASS_NAME, "ohlavicka3").text.strip()
                    points_log = [p.text.strip().replace('\n', ' ') for p in
                                  points_block_element.find_elements(By.CLASS_NAME, "pointlogg")]
                    pbp_data.append({"game_header": header_score, "points_log": points_log})
                except NoSuchElementException:
                    logging.warning("A PBP game block was malformed. Skipping.")
            return pbp_data
        except TimeoutException:
            logging.warning("Timed out waiting for PBP content to load. Match may not have PBP data.")
            return []
        except Exception as e:
            logging.error(f"A critical error occurred during PBP HTML scraping: {e}", exc_info=True)
            return []

    def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        if self.driver is None:
            logging.error(f"Driver is not started. Cannot fetch match {match_id}")
            return {}
        match_page_url = f"https://tenipo.com/match/-/{match_id}"
        logging.info(f"FETCHING data for match ID: {match_id}")
        try:
            del self.driver.requests
            self.driver.get(match_page_url)
            main_data_req = self.driver.wait_for_request(f'/xmlko/match{match_id}.xml', timeout=20)
            main_xml_str = self.driver.execute_script(
                "return janko(arguments[0]);", main_data_req.response.body.decode('latin-1'))
            if not main_xml_str:
                logging.error(f"Failed to get main match data for {match_id}. Aborting.")
                return {}
            parser = ET.XMLParser(recover=True, encoding='utf-8')
            main_root = ET.fromstring(main_xml_str.encode('utf-8'), parser=parser)
            combined_data = self._xml_to_dict(main_root)
            pbp_html_data = self._scrape_html_pbp()
            if pbp_html_data:
                logging.info(f"Successfully scraped {len(pbp_html_data)} PBP blocks from HTML for match {match_id}.")
            combined_data['point_by_point_html'] = pbp_html_data
            return combined_data
        except Exception as e:
            logging.error(f"FATAL: An unhandled error occurred in fetch_match_data for ID {match_id}: {e}",
                          exc_info=True)
            return {}

    def investigate_data_sources(self, match_id: str) -> List[str]:
        if self.driver is None:
            logging.error("Driver is not started. Cannot investigate.")
            return []
        logging.info(f"INVESTIGATING data sources for match ID: {match_id}")
        match_page_url = f"https://tenipo.com/match/-/{match_id}"
        try:
            del self.driver.requests
            self.driver.get(match_page_url)
            WebDriverWait(self.driver, 20).until(lambda d: len(d.requests) > 3)
            captured_urls = [req.url for req in self.driver.requests]
            logging.info(f"Captured {len(captured_urls)} requests for match {match_id}:")
            for url in captured_urls: logging.info(f"  - {url}")
            return captured_urls
        except Exception as e:
            logging.error(f"An error occurred during investigation for match ID {match_id}: {e}")
            return []