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
        # Initial page load to get cookies and JS context loaded.
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
        # This is a recursive function to convert lxml Element to a dictionary.
        result = {}
        if element.attrib: result.update(element.attrib)
        # Include element text if it's not just whitespace
        if element.text and element.text.strip(): result['#text'] = element.text.strip()
        # Recursively process child elements
        for child in element:
            child_data = self._xml_to_dict(child)
            # If tag already exists, turn it into a list
            if child.tag in result:
                if not isinstance(result[child.tag], list):
                    result[child.tag] = [result[child.tag]]
                result[child.tag].append(child_data)
            else:
                result[child.tag] = child_data
        return result

    def get_live_matches_summary(self) -> List[Dict[str, Any]]:
        # Fetches the main list of all live matches.
        try:
            del self.driver.requests
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))
            # The summary data is in a request to 'change2.xml'
            request = self.driver.wait_for_request(r'/change2\.xml', timeout=25)
            payload_str = request.response.body.decode('latin-1')
            decoded_xml_string = self.driver.execute_script("return janko(arguments[0]);", payload_str)

            if not decoded_xml_string:
                logging.warning("Match summary XML was empty after decoding.")
                return []

            parser = ET.XMLParser(recover=True, encoding='utf-8')
            root = ET.fromstring(decoded_xml_string.encode('utf-8'), parser=parser)

            if root is None:
                logging.warning("Could not parse match summary XML.")
                return []

            # The site uses 'match' or 'event' tags for the items.
            match_tags = root.findall("./match") or root.findall("./event")
            logging.info(f"Found {len(match_tags)} match/event tags in summary XML.")
            return [self._xml_to_dict(tag) for tag in match_tags]
        except Exception as e:
            logging.error(f"Error in get_live_matches_summary: {e}", exc_info=True)
            return []

    def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        """
        Fetches all data for a single match, including the critical Point-by-Point (PBP) data.
        This function is built to be resilient and methodical.
        """
        match_page_url = f"https://tenipo.com/match/-/{match_id}"
        logging.info(f"FETCHING data for match ID: {match_id}")

        try:
            # --- STAGE 1: Get the main match data XML ---
            del self.driver.requests
            self.driver.get(match_page_url)

            main_data_req = self.driver.wait_for_request(f'/xmlko/match{match_id}.xml', timeout=20)
            main_xml_str = self.driver.execute_script(
                "return janko(arguments[0]);",
                main_data_req.response.body.decode('latin-1')
            )
            if not main_xml_str:
                logging.error(f"Failed to get main match data for {match_id}. Aborting.")
                return {}

            # --- STAGE 2: Get the Point-by-Point (PBP) data ---
            pbp_xml_str = ""
            try:
                # Find the "PT BY PT" tab on the page.
                pbp_tab_xpath = "//div[contains(@class, 'subNavigationButton') and text()='PT BY PT']"
                wait = WebDriverWait(self.driver, 10)
                pbp_tab_element = wait.until(EC.element_to_be_clickable((By.XPATH, pbp_tab_xpath)))

                # CRUCIAL: Clear requests that loaded with the page *before* we click for new data.
                del self.driver.requests

                # A JavaScript click can be more reliable.
                self.driver.execute_script("arguments[0].click();", pbp_tab_element)
                logging.info(f"Clicked 'PT BY PT' tab for match {match_id}.")

                # Now, wait specifically for the PBP data file triggered by the click.
                pbp_req = self.driver.wait_for_request(f'/xmlko/matchl{match_id}.xml', timeout=20)
                logging.info(f"Captured PBP data request for {match_id}.")

                if pbp_req and pbp_req.response and pbp_req.response.body:
                    pbp_payload_str = pbp_req.response.body.decode('latin-1')

                    # This is the key check: only try to decode if it looks like the encoded data.
                    # The encoded string seems to be wrapped in an <l> tag.
                    if '<l>' in pbp_payload_str:
                        logging.info(f"PBP payload for {match_id} appears to be encoded. Attempting to decode.")
                        pbp_xml_str = self.driver.execute_script("return janko(arguments[0]);", pbp_payload_str)
                    else:
                        # If it's not encoded, it might be plain XML or an error. Don't try to decode.
                        logging.warning(
                            f"PBP payload for {match_id} does not appear to be encoded. Skipping decode. Payload: {pbp_payload_str[:200]}")

            except TimeoutException:
                logging.warning(
                    f"Timed out waiting for PBP data for match {match_id}. It may not exist for this match.")
            except WebDriverException as e:
                # This catches the 'atob' error if our '<l>' check isn't perfect.
                logging.error(f"A WebDriver error occurred decoding PBP for {match_id}. Error: {e}")
            except Exception as e:
                logging.error(f"An unexpected error occurred fetching PBP data for {match_id}: {e}", exc_info=True)

            # --- STAGE 3: Combine and return the data ---
            parser = ET.XMLParser(recover=True, encoding='utf-8')
            main_root = ET.fromstring(main_xml_str.encode('utf-8'), parser=parser)
            combined_data = self._xml_to_dict(main_root)

            if pbp_xml_str:
                logging.info(f"Successfully decoded PBP data for {match_id}. Combining.")
                pbp_root = ET.fromstring(pbp_xml_str.encode('utf-8'), parser=parser)
                combined_data['point_by_point'] = self._xml_to_dict(pbp_root)
            else:
                logging.info(f"No PBP data was found or decoded for {match_id}.")
                combined_data['point_by_point'] = {}  # Ensure key exists for the data mapper

            return combined_data

        except TimeoutException:
            logging.error(
                f"FATAL: Timed out waiting for the MAIN match data for {match_id}. The site may be down or URL is wrong.")
            return {}
        except Exception as e:
            logging.error(f"FATAL: An unhandled error occurred in fetch_match_data for ID {match_id}: {e}",
                          exc_info=True)
            return {}

    def close(self):
        if self.driver:
            self.driver.quit()