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

    def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        match_page_url = f"https://tenipo.com/match/-/{match_id}"
        try:
            del self.driver.requests
            self.driver.get(match_page_url)

            # Fetch both data files simultaneously
            match_req = self.driver.wait_for_request(f'/xmlko/match{match_id}.xml', timeout=20)
            pbp_req = self.driver.wait_for_request(f'/xmlko/matchl{match_id}.xml', timeout=20)

            # Decode the main match data
            match_payload_str = match_req.response.body.decode('latin-1')
            match_xml_str = self.driver.execute_script("return janko(arguments[0]);", match_payload_str)
            if not match_xml_str: return {}

            # Decode the point-by-point data
            pbp_payload_str = pbp_req.response.body.decode('latin-1')
            pbp_xml_str = self.driver.execute_script("return janko(arguments[0]);", pbp_payload_str)

            parser = ET.XMLParser(recover=True, encoding='utf-8')
            match_root = ET.fromstring(match_xml_str.encode('utf-8'), parser=parser)
            pbp_root = ET.fromstring(pbp_xml_str.encode('utf-8'), parser=parser) if pbp_xml_str else None

            # Combine the data
            combined_data = self._xml_to_dict(match_root)
            if pbp_root is not None:
                combined_data['point_by_point'] = self._xml_to_dict(pbp_root)

            return combined_data

        except Exception as e:
            logging.error(f"Error in fetch_match_data for ID {match_id}: {e}", exc_info=True)
            return {}

    def close(self):
        if self.driver:
            self.driver.quit()