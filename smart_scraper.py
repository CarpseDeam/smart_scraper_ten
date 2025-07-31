import logging
from lxml import etree as ET
from typing import List, Dict, Any

import config
from seleniumwire import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException, TimeoutException, NoSuchElementException
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

    def _scrape_html_pbp(self) -> List[Dict[str, Any]]:
        """
        Scrapes the Point-by-Point tab from the rendered HTML, using the correct
        parallel structure for headers and point blocks.
        """
        pbp_data = []
        try:
            # 1. Click the "PT BY PT" tab to make the data visible.
            pbp_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.ID, "buttonhistoryall"))
            )
            self.driver.execute_script("arguments[0].click();", pbp_button)
            logging.info("Clicked 'PT BY PT' tab.")

            # 2. Wait for the main container of the PBP data to appear.
            pbp_container = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "history"))
            )

            # 3. Find the parallel lists of header blocks and point blocks.
            game_headers = pbp_container.find_elements(By.CLASS_NAME, "ohlavicka1")
            game_point_blocks = pbp_container.find_elements(By.CLASS_NAME, "sethistory")
            logging.info(f"Found {len(game_headers)} game headers and {len(game_point_blocks)} point blocks.")

            # 4. Zip them together and process each game as a pair.
            for header_element, points_block_element in zip(game_headers, game_point_blocks):
                try:
                    header_score = header_element.find_element(By.CLASS_NAME, "ohlavicka3").text.strip()
                    points_log = [p.text.strip().replace('\n', ' ') for p in
                                  points_block_element.find_elements(By.CLASS_NAME, "pointlogg")]

                    pbp_data.append({
                        "game_header": header_score,
                        "points_log": points_log
                    })
                except NoSuchElementException:
                    logging.warning("A PBP game block was malformed (missing header or points). Skipping.")
                except Exception as e:
                    logging.warning(f"Could not parse a specific PBP game/point pair: {e}")

            return pbp_data

        except TimeoutException:
            logging.warning("Timed out waiting for PBP HTML content. It might not be available for this match.")
            return []
        except Exception as e:
            logging.error(f"A critical error occurred during PBP HTML scraping: {e}", exc_info=True)
            return []

    def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        """
        HYBRID APPROACH:
        1. Uses selenium-wire to efficiently get the main match XML data.
        2. Uses standard selenium to scrape the rendered HTML for PBP data.
        """
        match_page_url = f"https://tenipo.com/match/-/{match_id}"
        logging.info(f"FETCHING data for match ID: {match_id}")

        try:
            # --- STAGE 1: Get main data via XML interception ---
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

            parser = ET.XMLParser(recover=True, encoding='utf-8')
            main_root = ET.fromstring(main_xml_str.encode('utf-8'), parser=parser)
            combined_data = self._xml_to_dict(main_root)

            # --- STAGE 2: Scrape PBP data from rendered HTML ---
            pbp_html_data = self._scrape_html_pbp()
            if pbp_html_data:
                logging.info(f"Successfully scraped {len(pbp_html_data)} PBP blocks from HTML for match {match_id}.")
            else:
                logging.warning(f"No PBP data was scraped from HTML for match {match_id}.")

            combined_data['point_by_point_html'] = pbp_html_data

            return combined_data

        except Exception as e:
            logging.error(f"FATAL: An unhandled error occurred in fetch_match_data for ID {match_id}: {e}",
                          exc_info=True)
            return {}

    def close(self):
        if self.driver:
            self.driver.quit()