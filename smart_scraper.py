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
        Scrapes the Point-by-Point tab from the rendered HTML. This version
        correctly waits for the content itself, not a container.
        """
        pbp_data = []
        try:
            # 1. Click the "PT BY PT" tab.
            pbp_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.ID, "buttonhistoryall"))
            )
            self.driver.execute_script("arguments[0].click();", pbp_button)
            logging.info("Clicked 'PT BY PT' tab.")

            # 2. THE CORRECT WAIT: Wait for the first game header block to be present on the page.
            #    This is more reliable than waiting for a container that might not exist.
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "ohlavicka1"))
            )

            # 3. Now that we know the content is loaded, find all headers and point blocks.
            game_headers = self.driver.find_elements(By.CLASS_NAME, "ohlavicka1")
            game_point_blocks = self.driver.find_elements(By.CLASS_NAME, "sethistory")
            logging.info(f"Found {len(game_headers)} game headers and {len(game_point_blocks)} point blocks.")

            # 4. Process each game by pairing a header with its corresponding point block.
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
                    logging.warning("A PBP game block was malformed. Skipping.")

            return pbp_data

        except TimeoutException:
            logging.warning("Timed out waiting for PBP content to load after click. Match may not have PBP data.")
            return []
        except Exception as e:
            logging.error(f"A critical error occurred during PBP HTML scraping: {e}", exc_info=True)
            return []

    def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        """
        HYBRID APPROACH: Gets main data via XML and PBP data via direct HTML scraping.
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

    def investigate_data_sources(self, match_id: str) -> List[str]:
        """
        Placeholder method for the /investigate endpoint.
        Navigates to a match page and logs all intercepted request URLs.
        """
        logging.info(f"INVESTIGATING data sources for match ID: {match_id}")
        match_page_url = f"https://tenipo.com/match/-/{match_id}"
        try:
            del self.driver.requests
            self.driver.get(match_page_url)
            # Give page time to make various background requests
            WebDriverWait(self.driver, 20).until(
                lambda d: len(d.requests) > 3  # Wait until a few requests are captured
            )

            captured_urls = [req.url for req in self.driver.requests]
            logging.info(f"Captured {len(captured_urls)} requests for match {match_id}:")
            for url in captured_urls:
                logging.info(f"  - {url}")
            return captured_urls
        except Exception as e:
            logging.error(f"An error occurred during investigation for match ID {match_id}: {e}")
            return []