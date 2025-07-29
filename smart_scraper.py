import logging
import base64
import time
import xml.etree.ElementTree as ET

import config
from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import WebDriverException

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class TenipoScraper:
    """Manages the browser instance and orchestrates the scraping process using Selenium-Wire."""

    def __init__(self, settings: config.Settings):
        self.settings = settings
        self.driver: webdriver.Chrome = self._setup_driver()
        logging.info("TenipoScraper initialized and SeleniumWire WebDriver set up.")
        # Load the page once to get the decoder script into the browser's context.
        self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))
        logging.info("Initial page loaded to ensure decoder script is available.")

    def _setup_driver(self) -> webdriver.Chrome:
        chrome_options = Options()
        # All the arguments needed for stable container operation
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--single-process")
        chrome_options.add_argument("--user-data-dir=/tmp/chrome-user-data")
        chrome_options.add_argument(f"user-agent={self.settings.USER_AGENT}")

        seleniumwire_options = {'disable_encoding': True}

        try:
            service = Service()
            driver = webdriver.Chrome(service=service, options=chrome_options,
                                      seleniumwire_options=seleniumwire_options)
            time.sleep(2)  # Brief pause to prevent startup race conditions
            driver.set_page_load_timeout(45)
            logging.info("SeleniumWire WebDriver initialized successfully.")
            return driver
        except WebDriverException as e:
            logging.critical(f"Failed to set up Selenium WebDriver: {e}")
            raise

    def _decode_payload(self, payload: bytes) -> bytes:
        """Delegates decoding to the browser's native JavaScript 'janko' function."""
        try:
            base64_string = payload.decode('ascii')
            # This relies on the janko() function being available on the loaded page.
            js_script = "return janko(arguments[0]);"
            decoded_xml_string = self.driver.execute_script(js_script, base64_string)
            if not decoded_xml_string or not decoded_xml_string.strip().startswith('<'):
                logging.error(f"JavaScript decoding failed. Result: {decoded_xml_string[:200]}")
                return b""
            return decoded_xml_string.encode('utf-8')
        except Exception as e:
            logging.error(f"DECODING FAILED during JavaScript execution: {e}", exc_info=True)
            return b""

    def _xml_to_dict(self, element: ET.Element) -> dict:
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

    def get_live_matches_summary(self) -> list[dict]:
        """Fetches the summary data for all live matches from live2.xml."""
        try:
            logging.info(f"Fetching live matches summary...")
            request = self.driver.wait_for_request(self.settings.LIVE_FEED_DATA_URL, timeout=30)
            if not (request and request.response):
                logging.warning("Did not intercept a response for the live feed.")
                return []

            encrypted_content_bytes = request.response.body
            decompressed_xml_bytes = self._decode_payload(encrypted_content_bytes)
            if not decompressed_xml_bytes:
                raise ValueError("Payload decoding returned empty result.")

            root = ET.fromstring(decompressed_xml_bytes)
            live_matches = [self._xml_to_dict(match_tag) for match_tag in root.findall("./match")]
            logging.info(f"Found {len(live_matches)} total live matches in summary.")
            return live_matches
        except Exception as e:
            logging.error(f"An error occurred in get_live_matches_summary: {e}", exc_info=True)
            # Reload page in case of error to reset state
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))
            return []

    def fetch_match_data(self, match_id: str) -> dict:
        """Fetches and decodes detailed data for a single match ID."""
        match_xml_full_url = self.settings.MATCH_XML_URL_TEMPLATE.format(match_id=match_id)
        try:
            del self.driver.requests
            self.driver.execute_script(f"fetch('{match_xml_full_url}');")
            request = self.driver.wait_for_request(match_xml_full_url, timeout=30)

            if not (request and request.response):
                logging.error(f"Did not get a response when fetching {match_xml_full_url}")
                return {}

            encrypted_body_bytes = request.response.body
            if not encrypted_body_bytes:
                return {}

            decompressed_xml_bytes = self._decode_payload(encrypted_body_bytes)
            if not decompressed_xml_bytes:
                raise ValueError(f"Payload decoding returned empty result for match {match_id}")

            root = ET.fromstring(decompressed_xml_bytes)
            return self._xml_to_dict(root)
        except Exception as e:
            logging.error(f"An unexpected error in fetch_match_data for ID {match_id}: {e}", exc_info=True)
            return {}

    def close(self) -> None:
        if self.driver:
            self.driver.quit()
            logging.info("WebDriver closed.")