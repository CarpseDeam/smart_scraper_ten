import logging
import base64
import brotli  # <--- Using Brotli now
import json
import xml.etree.ElementTree as ET

import config
from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class TenipoScraper:
    def __init__(self, settings: config.Settings) -> None:
        self.settings = settings
        self.driver: webdriver.Chrome = self._setup_driver()
        logging.info("TenipoScraper initialized.")

    def _setup_driver(self) -> webdriver.Chrome:
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument(f"user-agent={self.settings.USER_AGENT}")
        seleniumwire_options = {'disable_encoding': True}
        try:
            driver = webdriver.Chrome(options=chrome_options, seleniumwire_options=seleniumwire_options)
            driver.set_page_load_timeout(30)
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

    def get_live_match_ids(self) -> list[str]:
        match_ids = []
        try:
            logging.info(f"Navigating to: {self.settings.LIVESCORE_PAGE_URL}")
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))
            logging.info(f"Waiting to intercept: '{self.settings.LIVE_FEED_DATA_URL}'...")
            request = self.driver.wait_for_request(self.settings.LIVE_FEED_DATA_URL, timeout=20)

            if not (request and request.response):
                logging.warning("Did not intercept a response for the live feed.")
                return []

            logging.info(f"Intercepted live feed: {request.url}")
            encrypted_content_bytes = request.response.body
            decoded_b64_bytes = base64.b64decode(encrypted_content_bytes)

            # =========================================================
            # ===> TESTING BROTLI DECOMPRESSION <===
            decompressed_xml_bytes = brotli.decompress(decoded_b64_bytes)
            # =========================================================

            root = ET.fromstring(decompressed_xml_bytes)
            for event_tag in root.findall(".//event"):
                if match_id := event_tag.get("id"):
                    match_ids.append(match_id)
            logging.info(f"Successfully decoded. Found {len(match_ids)} match IDs.")

        except Exception as e:
            logging.error(f"An error occurred in get_live_match_ids: {e}")

        return match_ids

    def fetch_match_data(self, match_id: str) -> dict:
        try:
            del self.driver.requests
            match_xml_full_url = self.settings.MATCH_XML_URL_TEMPLATE.format(match_id=match_id)
            logging.info(f"Waiting to intercept: '{match_xml_full_url}'...")
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))
            request = self.driver.wait_for_request(match_xml_full_url, timeout=20)

            if not (request and request.response):
                logging.warning(f"Did not intercept a response for match {match_id}.")
                return {}

            logging.info(f"Intercepted match data: {request.url}")
            encrypted_body_bytes = request.response.body
            decoded_b64_bytes = base64.b64decode(encrypted_body_bytes)

            # =========================================================
            # ===> Applying Brotli fix here too <===
            decompressed_xml_bytes = brotli.decompress(decoded_b64_bytes)
            # =========================================================

            root = ET.fromstring(decompressed_xml_bytes)
            return self._xml_to_dict(root)

        except Exception as e:
            logging.error(f"An unexpected error occurred in fetch_match_data for ID {match_id}: {e}")

        return {}

    def run(self) -> None:
        logging.info("Starting run...")
        try:
            live_match_ids = self.get_live_match_ids()
            if not live_match_ids:
                logging.warning("No live match IDs found.")
                return

            first_match_id = live_match_ids[0]
            logging.info(f"Processing first match: {first_match_id}")
            match_data = self.fetch_match_data(first_match_id)

            if match_data:
                print(json.dumps(match_data, indent=2, ensure_ascii=False))
        finally:
            self.close()

    def close(self) -> None:
        if self.driver:
            self.driver.quit()
            logging.info("WebDriver closed.")


if __name__ == "__main__":
    try:
        app_settings = config.Settings()
        scraper_instance = TenipoScraper(app_settings)
        scraper_instance.run()
    except Exception as e:
        logging.critical(f"Application failed: {e}", exc_info=True)