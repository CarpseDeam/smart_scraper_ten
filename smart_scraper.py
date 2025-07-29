import logging
import base64
import json
import time # Import the time module
import xml.etree.ElementTree as ET

import config
from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service # Import the Service object
from selenium.common.exceptions import TimeoutException, WebDriverException

# Configure logging for the module
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class TenipoScraper:
    """Manages the browser instance and orchestrates the scraping process."""

    def __init__(self, settings: config.Settings) -> None:
        self.settings = settings
        self.driver: webdriver.Chrome = self._setup_driver()
        logging.info("TenipoScraper initialized and SeleniumWire WebDriver set up.")

    def _setup_driver(self) -> webdriver.Chrome:
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-in-process-stack-traces")
        chrome_options.add_argument("--disable-logging")
        chrome_options.add_argument("--log-level=3")
        chrome_options.add_argument("--single-process")
        chrome_options.add_argument("--user-data-dir=/tmp/chrome-user-data")
        chrome_options.add_argument("--remote-debugging-port=9222")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument(f"user-agent={self.settings.USER_AGENT}")

        seleniumwire_options = {'disable_encoding': True}

        try:
            # --- ROBUST STARTUP FIX ---
            # 1. Explicitly manage the chromedriver process using a Service object.
            # This is more stable and helps prevent zombie processes.
            service = Service()

            # 2. Instantiate the driver using the service.
            driver = webdriver.Chrome(service=service, options=chrome_options, seleniumwire_options=seleniumwire_options)

            # 3. Add a small delay to prevent race conditions on startup.
            # This gives the browser process a moment to fully initialize.
            logging.info("Driver instantiated, pausing for 2 seconds to ensure stability...")
            time.sleep(2)
            # --- END FIX ---

            driver.set_page_load_timeout(30)
            logging.info("SeleniumWire WebDriver initialized successfully.")
            return driver
        except WebDriverException as e:
            logging.critical(f"Failed to set up Selenium WebDriver: {e}")
            raise

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

    def _decode_payload(self, payload: bytes) -> bytes:
        try:
            base64_string = payload.decode('ascii')
            js_script = "return janko(arguments[0]);"
            decoded_xml_string = self.driver.execute_script(js_script, base64_string)
            if not decoded_xml_string or not decoded_xml_string.strip().startswith('<'):
                logging.error(
                    f"JavaScript decoding failed or returned invalid data. Result: {decoded_xml_string[:200]}")
                return b""
            final_xml_bytes = decoded_xml_string.encode('utf-8')
            return final_xml_bytes
        except Exception as e:
            logging.error(f"DECODING FAILED during JavaScript execution: {e}", exc_info=True)
            return b""

    def get_live_matches_summary(self) -> list[dict]:
        try:
            logging.info(f"Fetching live matches summary...")
            if "livescore" not in self.driver.current_url:
                self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))

            request = self.driver.wait_for_request(self.settings.LIVE_FEED_DATA_URL, timeout=20)
            if not (request and request.response):
                logging.warning("Did not intercept a response for the live feed in get_live_matches_summary.")
                return []

            logging.info(f"Intercepted live feed: {request.url}. Now decoding payload.")
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
            return []

    def fetch_match_data(self, match_id: str) -> dict:
        try:
            if "livescore" not in self.driver.current_url:
                self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))

            match_xml_full_url = self.settings.MATCH_XML_URL_TEMPLATE.format(match_id=match_id)
            logging.info(f"Triggering background fetch for: {match_xml_full_url}")

            del self.driver.requests
            self.driver.execute_script(f"fetch('{match_xml_full_url}');")

            request = self.driver.wait_for_request(match_xml_full_url, timeout=20)
            logging.info(f"Intercepted match data: {request.url}")

            if not (request and request.response):
                logging.error(f"Did not get a response when fetching {match_xml_full_url}")
                return {}

            encrypted_body_bytes = request.response.body
            if not encrypted_body_bytes:
                logging.warning(f"Response body for match {match_id} was empty.")
                return {}

            decompressed_xml_bytes = self._decode_payload(encrypted_body_bytes)
            if not decompressed_xml_bytes:
                raise ValueError(f"Payload decoding returned empty result for match {match_id}")

            root = ET.fromstring(decompressed_xml_bytes)
            logging.info(f"Successfully fetched and decoded data for match {match_id}")
            return self._xml_to_dict(root)
        except Exception as e:
            logging.error(f"An unexpected error occurred in fetch_match_data for ID {match_id}: {e}", exc_info=True)
            return {}

    def close(self) -> None:
        if self.driver:
            self.driver.quit()
            logging.info("WebDriver closed.")