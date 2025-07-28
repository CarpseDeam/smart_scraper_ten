import logging
import base64
import json
import xml.etree.ElementTree as ET

import config
from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options
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
        chrome_options.add_argument(f"user-agent={self.settings.USER_AGENT}")
        seleniumwire_options = {'disable_encoding': True}
        try:
            driver = webdriver.Chrome(options=chrome_options, seleniumwire_options=seleniumwire_options)
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
        """
        Delegates decoding to the browser's native JavaScript engine,
        calling the website's own `janko()` function to avoid translation errors.
        """
        try:
            # The payload from selenium-wire is bytes, but the JS function
            # expects a Base64 string. We decode it to a plain ASCII string.
            base64_string = payload.decode('ascii')

            # This JavaScript snippet calls the page's `janko` function.
            # `arguments[0]` is how Selenium passes parameters to the script.
            js_script = "return janko(arguments[0]);"

            # Execute the script and get the clean XML string back.
            decoded_xml_string = self.driver.execute_script(js_script, base64_string)

            if not decoded_xml_string or not decoded_xml_string.strip().startswith('<'):
                logging.error(
                    f"JavaScript decoding failed or returned invalid data. Result: {decoded_xml_string[:200]}")
                return b""

            # Encode the resulting string into bytes for the XML parser.
            final_xml_bytes = decoded_xml_string.encode('utf-8')

            return final_xml_bytes
        except Exception as e:
            logging.error(f"DECODING FAILED during JavaScript execution: {e} ({type(e).__name__})")
            return b""

    def get_live_match_ids(self) -> list[str]:
        match_ids = []
        try:
            logging.info(f"Navigating to: {self.settings.LIVESCORE_PAGE_URL}")
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))

            logging.info(f"Waiting for page to load and request '{self.settings.LIVE_FEED_DATA_URL}'...")
            request = self.driver.wait_for_request(self.settings.LIVE_FEED_DATA_URL, timeout=20)

            if not (request and request.response):
                logging.warning("Did not intercept a response for the live feed.")
                return []

            logging.info(f"Intercepted live feed: {request.url}. Now decoding payload.")
            encrypted_content_bytes = request.response.body

            decompressed_xml_bytes = self._decode_payload(encrypted_content_bytes)
            if not decompressed_xml_bytes:
                raise ValueError("Payload decoding returned empty result.")

            logging.info(
                f"DECODED LIVE FEED XML (first 500 chars): {decompressed_xml_bytes.decode('utf-8', errors='ignore')[:500]}")

            root = ET.fromstring(decompressed_xml_bytes)

            # THE FINAL FIX: The log showed the tag is <match>, not <event>.
            for match_tag in root.findall(".//match"):
                if match_id := match_tag.get("id"):
                    match_ids.append(match_id)

            if not match_ids:
                logging.warning("XML was decoded successfully, but no <match> tags with 'id' attributes were found.")
            else:
                logging.info(f"Successfully decoded. Found {len(match_ids)} match IDs.")

        except ET.ParseError as e:
            logging.error(f"XML ParseError in get_live_match_ids: {e}. The decoded content is likely not valid XML.")
            return []
        except Exception as e:
            logging.error(f"An error occurred in get_live_match_ids: {e}")

        return match_ids

    def fetch_match_data(self, match_id: str) -> dict:
        """
        Fetches and decodes data for a single match ID by triggering a fetch
        from within the browser and intercepting the response.
        """
        try:
            # Ensure we are on the main page so that janko() is available
            if "livescore" not in self.driver.current_url:
                logging.info("Not on the livescore page, navigating back to ensure decoder is available.")
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

            logging.info(
                f"DECODED MATCH XML (first 500 chars): {decompressed_xml_bytes.decode('utf-8', errors='ignore')[:500]}")

            root = ET.fromstring(decompressed_xml_bytes)
            logging.info(f"Successfully fetched and decoded data for match {match_id}")
            return self._xml_to_dict(root)

        except TimeoutException:
            logging.error(f"Timeout occurred while waiting for match data for ID {match_id}.")
            return {}
        except Exception as e:
            logging.error(f"An unexpected error occurred in fetch_match_data for ID {match_id}: {e}", exc_info=True)
            return {}

    def run(self) -> None:
        """Main execution block for the scraper."""
        logging.info("Starting scraper run...")
        try:
            live_match_ids = self.get_live_match_ids()
            if not live_match_ids:
                logging.warning("No live match IDs found. Exiting.")
                return

            logging.info(f"Found {len(live_match_ids)} live matches. Processing up to the first 3 as a demonstration.")

            for match_id in live_match_ids[:3]:
                logging.info(f"--- Processing match: {match_id} ---")
                match_data = self.fetch_match_data(match_id)

                if match_data:
                    print(f"\n--- SUCCESS! DECODED DATA for match {match_id} ---")
                    print(json.dumps(match_data, indent=2, ensure_ascii=False))
                    print("--- END OF DATA ---\n")
                else:
                    logging.warning(f"Could not retrieve or process data for match {match_id}.")

        except Exception as e:
            logging.critical(f"A critical error occurred during the run: {e}", exc_info=True)
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