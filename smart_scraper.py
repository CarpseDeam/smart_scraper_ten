import logging
import base64
from lxml import etree as ET
from typing import List, Dict, Any

import config
from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException, TimeoutException

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class TenipoScraper:
    def __init__(self, settings: config.Settings):
        self.settings = settings
        self.driver: webdriver.Chrome = self._setup_driver()
        # The page load ensures the site's JavaScript (including the decoder) is ready.
        self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))
        logging.info("TenipoScraper (Selenium) initialized and page context loaded.")

    def _setup_driver(self) -> webdriver.Chrome:
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
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

    def get_live_matches_summary(self) -> List[Dict[str, Any]]:
        try:
            del self.driver.requests

            logging.info("Waiting for 'change2.xml' data request...")
            request = self.driver.wait_for_request(r'/change2\.xml', timeout=25)
            logging.info(f"'change2.xml' request captured (size: {len(request.response.body)} bytes).")

            # THE FIX: We use the browser's own JavaScript decoder function.
            # The function `janko()` is loaded when we visit the livescore page.
            # We pass the raw response body, base64 encoded, to the script.

            # Step 1: Base64 encode the raw bytes so we can pass it as a clean string to JavaScript
            payload_b64 = base64.b64encode(request.response.body).decode('ascii')

            # Step 2: Execute the site's own decoder function on the payload
            logging.info("Executing site's native JavaScript decoder...")
            decoded_xml_string = self.driver.execute_script("return janko(arguments[0]);", payload_b64)

            if not decoded_xml_string:
                logging.warning("JavaScript decoder returned an empty result.")
                return []

            logging.info(f"Successfully decoded payload using JS. (First 100 bytes: {decoded_xml_string[:100]})")

            # Step 3: Parse the now-clean XML
            parser = ET.XMLParser(recover=True)
            root = ET.fromstring(decoded_xml_string.encode('utf-8'), parser=parser)

            if root is None:
                logging.error("XML parsing failed even after successful JS decoding. The structure might have changed.")
                return []

            match_tags = root.findall("./match")
            if not match_tags:
                match_tags = root.findall("./event")

            logging.info(f"Found {len(match_tags)} match/event tags in the XML.")
            return [self._xml_to_dict(tag) for tag in match_tags]

        except TimeoutException:
            logging.warning(
                "TIMEOUT: No 'change2.xml' request was detected. This is normal if no ITF matches are live.")
            return []
        except Exception as e:
            logging.error(f"An unexpected error occurred in get_live_matches_summary: {e}", exc_info=True)
            return []

    def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        match_xml_full_url = self.settings.MATCH_XML_URL_TEMPLATE.format(match_id=match_id)
        try:
            del self.driver.requests
            request = self.driver.wait_for_request(match_xml_full_url, timeout=20)
            if not (request and request.response): return {}

            payload_b64 = base64.b64encode(request.response.body).decode('ascii')
            decoded_xml_string = self.driver.execute_script("return janko(arguments[0]);", payload_b64)

            if not decoded_xml_string: return {}

            parser = ET.XMLParser(recover=True)
            root = ET.fromstring(decoded_xml_string.encode('utf-8'), parser=parser)

            if root is None: return {}

            return self._xml_to_dict(root)
        except Exception as e:
            logging.error(f"Error in fetch_match_data for ID {match_id}: {e}")
            return {}

    def close(self):
        if self.driver:
            self.driver.quit()