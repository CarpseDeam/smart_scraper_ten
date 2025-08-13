import logging
import os
import shutil
import uuid
import json
from lxml import etree as ET
from typing import List, Dict, Any

import config
from selenium import webdriver
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
        if self.driver is None:
            logging.info("Initializing new Selenium driver...")
            self.driver = self._setup_driver()

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

        logging_prefs = {'performance': 'ALL'}
        chrome_options.set_capability('goog:loggingPrefs', logging_prefs)

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

    def _get_decoded_xml_from_request_url(self, url_pattern: str) -> str | None:
        # This is the original, flawed data-finding logic that we are reverting to
        # because it was at least finding *some* data before the crashes.
        # It is not perfect, but it is what was running during the successful logs.
        try:
            logs = self.driver.get_log('performance')
            for log_entry in logs:
                try:
                    message = json.loads(log_entry.get('message', '{}')).get('message', {})
                    if 'Network.responseReceived' in message.get('method', ''):
                        params = message.get('params', {})
                        url = params.get('response', {}).get('url', '')
                        if url_pattern in url:
                            request_id = params.get('requestId')
                            if request_id:
                                body_data = self.driver.execute_cdp_cmd('Network.getResponseBody',
                                                                        {'requestId': request_id})
                                payload_str = body_data.get('body', '')
                                if payload_str:
                                    return self.driver.execute_script("return janko(arguments[0]);", payload_str)
                except Exception:
                    continue
        except Exception as e:
            logging.error(f"Could not get decoded XML for pattern '{url_pattern}': {e}")
        return None

    def get_live_matches_summary(self) -> List[Dict[str, Any]]:
        if self.driver is None: return []
        try:
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))
            # The original logic did not have an explicit wait here.
            # We are reverting to that to match the working logs.
            decoded_xml_string = self._get_decoded_xml_from_request_url("change2.xml")
            if not decoded_xml_string:
                logging.warning("Could not find change2.xml in network traffic.")
                return []

            parser = ET.XMLParser(recover=True, encoding='utf-8')
            root = ET.fromstring(decoded_xml_string.encode('utf-8'), parser=parser)
            if root is None: return []
            match_tags = root.findall("./match") or root.findall("./event")
            logging.info(f"Found {len(match_tags)} match/event tags in summary XML.")
            return [self._xml_to_dict(tag) for tag in match_tags]
        except Exception as e:
            logging.error(f"Error in get_live_matches_summary: {e}", exc_info=True)
            return []

    def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        if self.driver is None: return {}
        match_page_url = f"https://tenipo.com/match/-/{match_id}"
        logging.info(f"FETCHING data for match ID: {match_id}")
        try:
            self.driver.get(match_page_url)
            # The original logic did not have an explicit wait here.
            # We are reverting to that to match the working logs.

            main_xml_str = self._get_decoded_xml_from_request_url(f"match{match_id}.xml")
            if not main_xml_str:
                logging.error(f"Failed to get main match data for {match_id}.")
                return {}

            parser = ET.XMLParser(recover=True, encoding='utf-8')
            main_root = ET.fromstring(main_xml_str.encode('utf-8'), parser=parser)
            combined_data = self._xml_to_dict(main_root)

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
        if self.driver is None: return []
        pbp_data = []
        try:
            pbp_button = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.ID, "buttonhistoryall")))
            self.driver.execute_script("arguments[0].click();", pbp_button)
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "ohlavicka1")))
            game_headers = self.driver.find_elements(By.CLASS_NAME, "ohlavicka1")
            game_point_blocks = self.driver.find_elements(By.CLASS_NAME, "sethistory")
            for header, block in zip(game_headers, game_point_blocks):
                try:
                    score = header.find_element(By.CLASS_NAME, "ohlavicka3").text.strip()
                    points = [p.text.strip().replace('\n', ' ') for p in
                              block.find_elements(By.CLASS_NAME, "pointlogg")]
                    pbp_data.append({"game_header": score, "points_log": points})
                except NoSuchElementException:
                    continue
            return pbp_data
        except TimeoutException:
            return []
        except Exception as e:
            logging.error(f"PBP scraping error: {e}", exc_info=True)
            return []