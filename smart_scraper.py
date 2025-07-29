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
        logging.info("TenipoScraper (Selenium) initialized.")

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

    def _decode_payload(self, payload: bytes) -> bytes:
        if not payload: return b""
        try:
            decoded_b64_bytes = base64.b64decode(payload)
            data_len = len(decoded_b64_bytes)
            if data_len == 0: return b""
            char_list = []
            for i, byte_val in enumerate(decoded_b64_bytes):
                shift = (i % data_len - i % 4) * data_len + 64
                new_char_code = (byte_val - shift) % 256
                char_list.append(chr(new_char_code))
            second_base64_string = "".join(char_list)
            missing_padding = len(second_base64_string) % 4
            if missing_padding:
                second_base64_string += '=' * (4 - missing_padding)
            final_xml_bytes = base64.b64decode(second_base64_string.encode('latin-1'))
            return final_xml_bytes
        except Exception:
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

    def get_live_matches_summary(self) -> List[Dict[str, Any]]:
        try:
            del self.driver.requests
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))

            # Step 1: Patiently wait for the key data file.
            # This is the most important event. If it doesn't happen, there's likely no live data to get.
            logging.info("Waiting for 'change2.xml' data request...")
            self.driver.wait_for_request(r'/change2\.xml', timeout=25)
            logging.info("'change2.xml' request captured.")

            # Step 2: Find all 'change2.xml' requests that we've captured.
            candidate_requests = [
                r for r in self.driver.requests
                if r.response and '/change2.xml' in r.path
            ]

            if not candidate_requests:
                logging.warning(
                    "wait_for_request succeeded, but no 'change2.xml' requests found in capture list. This is unusual.")
                return []

            # Step 3: Pick the best one (largest payload) and process it.
            best_request = sorted(candidate_requests, key=lambda r: len(r.response.body), reverse=True)[0]
            logging.info(
                f"Processing best candidate '{best_request.path}' (size: {len(best_request.response.body)} bytes).")

            decompressed_xml_bytes = self._decode_payload(best_request.response.body)
            if not decompressed_xml_bytes:
                logging.warning(f"Payload from '{best_request.path}' was empty after decoding.")
                return []

            parser = ET.XMLParser(recover=True)
            root = ET.fromstring(decompressed_xml_bytes, parser=parser)
            if root is None:
                logging.warning("Could not parse XML from decoded payload.")
                return []

            match_tags = root.findall("./match")
            if not match_tags:
                match_tags = root.findall("./event")

            logging.info(f"Found {len(match_tags)} match/event tags in the XML.")
            return [self._xml_to_dict(tag) for tag in match_tags]

        except TimeoutException:
            logging.warning("TIMEOUT: No 'change2.xml' request was detected in the last 25 seconds.")
            logging.warning("This is normal if there are no live ITF matches currently running.")
            return []
        except Exception as e:
            logging.error(f"An unexpected error occurred in get_live_matches_summary: {e}", exc_info=True)
            return []

    def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        match_xml_full_url = self.settings.MATCH_XML_URL_TEMPLATE.format(match_id=match_id)
        try:
            del self.driver.requests
            # We still visit the main page to ensure scripts are loaded for subsequent requests.
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))
            request = self.driver.wait_for_request(match_xml_full_url, timeout=20)
            if not (request and request.response): return {}

            decompressed_xml_bytes = self._decode_payload(request.response.body)
            if not decompressed_xml_bytes: return {}

            parser = ET.XMLParser(recover=True)
            root = ET.fromstring(decompressed_xml_bytes, parser=parser)
            if root is None: return {}

            return self._xml_to_dict(root)
        except Exception as e:
            logging.error(f"Error in fetch_match_data for ID {match_id}: {e}")
            return {}

    def close(self):
        if self.driver:
            self.driver.quit()