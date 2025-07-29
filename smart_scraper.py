import logging
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
        # Load the page once to get the all-important janko() decoder into the browser context.
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
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))  # Refresh the page to get the latest updates

            logging.info("Waiting for 'change2.xml' summary data request...")
            request = self.driver.wait_for_request(r'/change2\.xml', timeout=25)
            logging.info(f"'change2.xml' request captured (size: {len(request.response.body)} bytes).")

            # Use the site's native JS decoder
            payload_str = request.response.body.decode('latin-1')
            decoded_xml_string = self.driver.execute_script("return janko(arguments[0]);", payload_str)

            if not decoded_xml_string:
                logging.warning("JavaScript decoder returned an empty result for summary.")
                return []

            logging.info(f"Successfully decoded summary payload using JS.")
            parser = ET.XMLParser(recover=True, encoding='utf-8')
            root = ET.fromstring(decoded_xml_string.encode('utf-8'), parser=parser)
            if root is None: return []

            match_tags = root.findall("./match")
            if not match_tags: match_tags = root.findall("./event")

            logging.info(f"Found {len(match_tags)} match/event tags in summary XML.")
            return [self._xml_to_dict(tag) for tag in match_tags]

        except TimeoutException:
            logging.warning("TIMEOUT: No 'change2.xml' request was detected. Normal if no ITF matches are live.")
            return []
        except Exception as e:
            logging.error(f"Error in get_live_matches_summary: {e}", exc_info=True)
            return []

    def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        match_xml_full_url = self.settings.MATCH_XML_URL_TEMPLATE.format(match_id=match_id)
        # Construct the URL to the specific match page to trigger the data load.
        match_page_url = f"https://tenipo.com/match/-/{match_id}"

        try:
            del self.driver.requests
            self.driver.get(match_page_url)

            logging.info(f"Waiting for detail request: {match_xml_full_url}")
            request = self.driver.wait_for_request(match_xml_full_url, timeout=20)
            logging.info(f"Detail request for match {match_id} captured.")

            # THE SAME FIX, APPLIED HERE: Use the site's native JS decoder
            payload_str = request.response.body.decode('latin-1')
            decoded_xml_string = self.driver.execute_script("return janko(arguments[0]);", payload_str)

            if not decoded_xml_string:
                logging.warning(f"JavaScript decoder returned empty result for match {match_id}.")
                return {}

            logging.info(f"Successfully decoded detail payload for match {match_id}.")
            parser = ET.XMLParser(recover=True, encoding='utf-8')
            root = ET.fromstring(decoded_xml_string.encode('utf-8'), parser=parser)
            if root is None: return {}

            return self._xml_to_dict(root)
        except TimeoutException:
            logging.error(
                f"TIMEOUT waiting for match detail XML for ID {match_id}. The page might not have loaded the data correctly.")
            return {}
        except Exception as e:
            logging.error(f"Error in fetch_match_data for ID {match_id}: {e}", exc_info=True)
            return {}

    def close(self):
        if self.driver:
            self.driver.quit()