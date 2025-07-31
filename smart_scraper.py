import logging
from lxml import etree as ET
from typing import List, Dict, Any

import config
from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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
            # === STAGE 1: Navigate and get main match data ===
            del self.driver.requests
            self.driver.get(match_page_url)

            # Wait for the main match data file and decode it
            match_req = self.driver.wait_for_request(f'/xmlko/match{match_id}.xml', timeout=20)

            match_xml_str = ""
            if match_req and match_req.response and match_req.response.body:
                match_payload_str = match_req.response.body.decode('latin-1')
                try:
                    match_xml_str = self.driver.execute_script("return janko(arguments[0]);", match_payload_str)
                except WebDriverException as e:
                    logging.error(
                        f"Could not decode main match data for {match_id}, aborting fetch for this match. Error: {e}")
                    return {}  # If main data fails, we can't proceed.

            if not match_xml_str:
                logging.warning(f"Main match data for {match_id} was empty after decoding. Aborting.")
                return {}

            # === STAGE 2: Get Point-by-Point data (PBP) ===
            pbp_xml_str = ""
            try:
                # Clear any requests that loaded with the page, before we click for PBP data
                del self.driver.requests

                # Find the "PT BY PT" tab and click it to trigger the data load
                pbp_tab_xpath = "//div[contains(@class, 'subNavigationButton') and text()='PT BY PT']"
                wait = WebDriverWait(self.driver, 10)
                pbp_tab_element = wait.until(EC.element_to_be_clickable((By.XPATH, pbp_tab_xpath)))
                # A JS click can be more reliable than Selenium's native click.
                self.driver.execute_script("arguments[0].click();", pbp_tab_element)

                # Now, wait for the PBP data file that is loaded *after* the click
                pbp_req = self.driver.wait_for_request(f'/xmlko/matchl{match_id}.xml', timeout=20)

                if pbp_req and pbp_req.response and pbp_req.response.body:
                    pbp_payload_str = pbp_req.response.body.decode('latin-1')
                    # The 'atob' error happens here. We wrap this critical part in its own try/except.
                    try:
                        pbp_xml_str = self.driver.execute_script("return janko(arguments[0]);", pbp_payload_str)
                    except WebDriverException as e:
                        # This is the specific error you were seeing. We'll log it as a warning and move on.
                        logging.warning(f"Failed to decode PBP payload for {match_id}. It might be invalid. Error: {e}")
                        pbp_xml_str = ""  # Ensure it's empty on failure, so we can proceed without PBP data.

            except TimeoutException:
                logging.info(
                    f"PBP_FETCH: Timed out waiting for PBP data for match {match_id}. It may not be available.")
                # It's okay to not have PBP data, we can continue without it.
                pass
            except Exception as e:
                logging.warning(f"PBP_FETCH: An unexpected error occurred while fetching PBP data for {match_id}: {e}")
                # Also okay to continue without it.
                pass

            # === STAGE 3: Combine and return data ===
            parser = ET.XMLParser(recover=True, encoding='utf-8')
            match_root = ET.fromstring(match_xml_str.encode('utf-8'), parser=parser)

            pbp_root = None
            if pbp_xml_str:
                try:
                    pbp_root = ET.fromstring(pbp_xml_str.encode('utf-8'), parser=parser)
                except ET.XMLSyntaxError:
                    logging.warning(f"PBP data for {match_id} was not valid XML after decoding. Skipping PBP.")

            combined_data = self._xml_to_dict(match_root)
            if pbp_root is not None:
                combined_data['point_by_point'] = self._xml_to_dict(pbp_root)
            else:
                # Ensure the key exists for the data_mapper, even if empty.
                combined_data['point_by_point'] = {}

            return combined_data

        except TimeoutException:
            logging.error(
                f"CRITICAL_FETCH_FAIL: Timed out waiting for MAIN match data for ID {match_id}. The site might be slow or the URL pattern is wrong.")
            return {}
        except Exception as e:
            logging.error(f"CRITICAL_FETCH_FAIL: A fatal error occurred in fetch_match_data for ID {match_id}: {e}",
                          exc_info=True)
            return {}

    def close(self):
        if self.driver:
            self.driver.quit()