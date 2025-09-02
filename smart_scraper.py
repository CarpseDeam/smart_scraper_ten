# smart_scraper.py
import logging
import re
import time
from typing import List, Dict, Any

import config
from lxml import etree as ET
from lxml import html
from selenium import webdriver
from selenium.common.exceptions import (WebDriverException, TimeoutException,
                                        NoSuchElementException, StaleElementReferenceException)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class TenipoScraper:
    def __init__(self, settings: config.Settings):
        self.settings = settings
        self.driver: webdriver.Chrome | None = None

    def start_driver(self):
        if self.driver is None:
            logging.info("Initializing new Selenium driver...")
            self.driver = self._setup_driver()

            script_source = """
                window.interceptedResponses = window.interceptedResponses || {};
                const originalSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.send = function(body) {
                    this.addEventListener('load', function() {
                        try {
                            if (this.responseURL && this.responseURL.includes('.xml')) {
                                window.interceptedResponses[this.responseURL] = this.responseText;
                            }
                        } catch (e) { console.error('Interception script error:', e); }
                    });
                    originalSend.call(this, body);
                };
            """
            self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": script_source})

    def _setup_driver(self) -> webdriver.Chrome:
        chrome_options = Options()
        chrome_options.add_argument("--incognito")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--mute-audio")
        chrome_options.add_argument("--remote-debugging-port=0")
        chrome_options.add_argument(f"user-agent={self.settings.USER_AGENT}")
        chrome_options.add_experimental_option(
            "prefs", {"profile.managed_default_content_settings.images": 2}
        )
        try:
            driver = webdriver.Chrome(options=chrome_options)
            driver.set_page_load_timeout(30)
            return driver
        except WebDriverException as e:
            logging.critical(f"Failed to set up Selenium WebDriver: {e}")
            raise

    def close(self):
        if self.driver:
            self.driver.quit()
            self.driver = None

    def get_live_matches_summary(self) -> tuple[bool, List[Dict[str, Any]]]:
        """
        🏎️ SPEED OPTIMIZED: Gets live match summary with intelligent XML polling.
        Eliminates the 3-second sleep for maximum speed!
        """
        if self.driver is None:
            return False, []
        try:
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))

            # 🚀 SPEED OPTIMIZATION: Intelligent polling instead of blind sleep
            all_xml_bodies = self._wait_for_xml_data_intelligently()

            if not all_xml_bodies:
                logging.info("SPEED DISCOVERY: No XML feeds intercepted - page likely empty")
                return True, []

            all_parsed_matches = []
            parser = ET.XMLParser(recover=True, encoding='utf-8')
            for xml_body in all_xml_bodies:
                root = ET.fromstring(xml_body.encode('utf-8'), parser=parser)
                if root is None: continue
                for match_element in root.xpath('//match'):
                    match_data = self._xml_to_dict(match_element)
                    if 'id' in match_data:
                        all_parsed_matches.append(match_data)

            final_matches_map = {match['id']: match for match in all_parsed_matches}
            page_source = self.driver.page_source
            html_tree = html.fromstring(page_source)

            itf_matches = []
            itf_tournament_blocks = html_tree.xpath(
                "//div[contains(@class, 'table_round')][.//div[contains(@class, 'tournament_logo') and contains(@style, 'itf.png')]]")

            for block in itf_tournament_blocks:
                name_elements = block.xpath(".//span[contains(@style, 'font-weight:bold')]")
                tournament_name = name_elements[0].text_content().strip() if name_elements else "ITF Tournament"

                match_tables = block.xpath(".//table[contains(@id, 'table')]")
                for match_table in match_tables:
                    table_id = match_table.get('id', '')
                    match_id_search = re.search(r'\[(\d+)\]', table_id)
                    if not match_id_search:
                        continue

                    match_id = match_id_search.group(1)

                    if match_id not in final_matches_map:
                        continue

                    match_summary = final_matches_map[match_id]
                    match_summary['tournament_name'] = tournament_name

                    sets = []
                    # Extract tab index from table ID like "table1[560416]" -> "1"
                    tab_index_str = table_id[5] if len(table_id) > 5 and table_id.startswith("table") and table_id[5].isdigit() else "1"

                    for i in range(1, 6):
                        p1_id = f"set1{i}{tab_index_str}[{match_id}]"
                        p2_id = f"set2{i}{tab_index_str}[{match_id}]"

                        p1_el = match_table.find(f".//td[@id='{p1_id}']")
                        p2_el = match_table.find(f".//td[@id='{p2_id}']")

                        if p1_el is not None and p2_el is not None:
                            p1_text = p1_el.text_content().strip()
                            p2_text = p2_el.text_content().strip()
                            if p1_text or p2_text:
                                sets.append({"p1": p1_text, "p2": p2_text})
                            else:
                                break
                        else:
                            break

                    p1_game_el = match_table.find(f".//td[@id='game1{tab_index_str}[{match_id}]']")
                    p2_game_el = match_table.find(f".//td[@id='game2{tab_index_str}[{match_id}]']")

                    # Extract serving indicator - pattern: serve10[match_id] and serve20[match_id]
                    p1_serve_el = match_table.find(f".//td[@id='serve10[{match_id}]']")
                    p2_serve_el = match_table.find(f".//td[@id='serve20[{match_id}]']")
                    
                    serving_player = None
                    if p1_serve_el is not None:
                        serve_div = p1_serve_el.find(".//div")
                        if serve_div is not None and "servey" in serve_div.get("class", ""):
                            serving_player = 1
                    if p2_serve_el is not None:
                        serve_div = p2_serve_el.find(".//div")
                        if serve_div is not None and "servey" in serve_div.get("class", ""):
                            serving_player = 2

                    match_summary["live_score_data"] = {
                        "sets": sets,
                        "currentGame": {
                            "p1": p1_game_el.text_content().strip() if p1_game_el is not None else None,
                            "p2": p2_game_el.text_content().strip() if p2_game_el is not None else None,
                        },
                        "servingPlayer": serving_player
                    }
                    itf_matches.append(match_summary)

            logging.info(
                f"SPEED DISCOVERY: Found {len(final_matches_map)} total matches, {len(itf_matches)} ITF matches")
            return True, itf_matches

        except Exception as e:
            logging.error(f"Error in get_live_matches_summary: {e}", exc_info=True)
            return False, []

    def _wait_for_xml_data_intelligently(self) -> List[str]:
        """
        🚀 SPEED OPTIMIZATION: Intelligently polls for XML data instead of blind sleeping.
        Dramatically faster than the old 3-second sleep!
        """
        max_wait_time = 8  # Maximum time to wait for data
        poll_interval = 0.2  # Check every 200ms
        start_time = time.monotonic()

        while time.monotonic() - start_time < max_wait_time:
            xml_bodies = self._get_all_intercepted_xml_bodies()
            if xml_bodies:
                wait_time = time.monotonic() - start_time
                logging.info(f"⚡ SPEED WIN: Got XML data in {wait_time:.2f}s (vs old 3s sleep)!")
                return xml_bodies
            time.sleep(poll_interval)

        logging.warning("SPEED WARNING: XML data not ready after maximum wait time")
        return self._get_all_intercepted_xml_bodies()  # Return whatever we have

    def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        """
        🐌 SLOW LANE: Fetches detailed match data from individual match pages.
        Only called by the slow polling task for enrichment.
        """
        if self.driver is None: return {}
        match_page_url = f"https://tenipo.com/match/-/{match_id}"
        logging.info(f"🐌 FETCHING DETAILS for match ID: {match_id}")
        try:
            self.driver.get(match_page_url)
            main_xml_str = self._get_intercepted_xml_body(f"match{match_id}.xml", timeout=15)
            if not main_xml_str:
                logging.warning(f"🐌 No match.xml intercepted for {match_id}")
                return {"match": {}}

            parser = ET.XMLParser(recover=True, encoding='utf-8')
            main_root = ET.fromstring(main_xml_str.encode('utf-8'), parser=parser)
            combined_data = {"match": self._xml_to_dict(main_root)}

            combined_data['point_by_point_html'] = self._scrape_html_pbp()
            combined_data['statistics_html'] = self._scrape_html_statistics()
            return combined_data
        except Exception as e:
            logging.error(f"🐌 FATAL error fetching details for {match_id}: {e}", exc_info=True)
            return {}

    def _get_all_intercepted_xml_bodies(self) -> List[str]:
        """Gets all intercepted XML response bodies."""
        get_all_script = """
            const responses = window.interceptedResponses || {};
            const bodies = Object.values(responses);
            const processedBodies = [];
            window.interceptedResponses = {};
            for (const body of bodies) {
                try {
                    const decoded = janko(body);
                    processedBodies.push(decoded);
                } catch (e) {
                    if (typeof body === 'string' && body.trim().startsWith('<')) {
                        processedBodies.push(body);
                    }
                }
            }
            return processedBodies;
        """
        try:
            results = self.driver.execute_script(get_all_script)
            return results
        except WebDriverException as e:
            logging.error(f"Could not execute script to get XML bodies: {e}")
            return []

    def _get_intercepted_xml_body(self, url_pattern: str, timeout: int = 15) -> str | None:
        """Gets a specific intercepted XML response by URL pattern."""
        get_single_script = f"""
            const url = Object.keys(window.interceptedResponses || {{}}).find(k => k.includes('{url_pattern}'));
            if (!url) return null;
            const body = window.interceptedResponses[url];
            delete window.interceptedResponses[url];
            try {{ return janko(body); }}
            catch (e) {{ if (typeof body === 'string' && body.trim().startsWith('<')) {{ return body; }} }}
            return null;
        """
        end_time = time.monotonic() + timeout
        while time.monotonic() < end_time:
            try:
                result = self.driver.execute_script(get_single_script)
                if result:
                    return result
            except WebDriverException:
                time.sleep(0.25)
        return None

    def _xml_to_dict(self, element: ET.Element) -> dict:
        """Converts XML element to dictionary."""
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

    def _scrape_html_pbp(self) -> List[Dict[str, Any]]:
        """Scrapes point-by-point data from HTML."""
        if self.driver is None: return []
        try:
            pbp_button = WebDriverWait(self.driver, 7).until(EC.element_to_be_clickable((By.ID, "buttonhistoryall")))
            self.driver.execute_script("arguments[0].click();", pbp_button)
            WebDriverWait(self.driver, 7).until(EC.presence_of_element_located((By.CLASS_NAME, "ohlavicka1")))
            pbp_data = []
            game_headers = self.driver.find_elements(By.CLASS_NAME, "ohlavicka1")
            game_point_blocks = self.driver.find_elements(By.CLASS_NAME, "sethistory")
            for header, block in zip(game_headers, game_point_blocks):
                score = header.find_element(By.CLASS_NAME, "ohlavicka3").text.strip()
                points = [p.text.strip().replace('\n', ' ') for p in block.find_elements(By.CLASS_NAME, "pointlogg")]
                pbp_data.append({"game_header": score, "points_log": points})
            return pbp_data
        except (TimeoutException, StaleElementReferenceException, NoSuchElementException):
            return []
        except Exception as e:
            logging.error(f"Error during PBP scraping: {e}", exc_info=True)
            return []

    def _scrape_html_statistics(self) -> List[Dict[str, Any]]:
        """Scrapes statistics data from HTML."""
        if self.driver is None: return []
        try:
            stats_button = WebDriverWait(self.driver, 7).until(EC.element_to_be_clickable((By.ID, "buttonstatsall")))
            self.driver.execute_script("arguments[0].click();", stats_button)
            WebDriverWait(self.driver, 7).until(EC.presence_of_element_located((By.ID, "stats")))
            service_keywords = ["Aces", "Serve", "Faults", "Break Points"]
            service_stats, return_stats = [], []
            stat_rows = self.driver.find_elements(By.CLASS_NAME, "stat")
            for row in stat_rows:
                if "opacity: 0.5" in row.get_attribute("style"): continue
                stat_name = row.find_element(By.CLASS_NAME, "stat_name").text.strip()
                value_elements = row.find_elements(By.CLASS_NAME, "stat_col")
                if len(value_elements) < 2: continue
                p1_val = value_elements[0].text.strip().replace("\n", " ")
                p2_val = value_elements[1].text.strip().replace("\n", " ")
                stat_item = {"name": stat_name, "home": p1_val, "away": p2_val}
                if any(keyword in stat_name for keyword in service_keywords):
                    service_stats.append(stat_item)
                else:
                    return_stats.append(stat_item)
            if service_stats or return_stats:
                return [
                    {"groupName": "Service", "statisticsItems": service_stats},
                    {"groupName": "Return", "statisticsItems": return_stats}
                ]
        except (TimeoutException, StaleElementReferenceException, NoSuchElementException):
            return []
        except Exception as e:
            logging.error(f"Error during statistics scraping: {e}", exc_info=True)
        return []

    def investigate_data_sources(self, match_id: str) -> List[str]:
        """Debug method to investigate available data sources for a match."""
        if self.driver is None: return []
        match_page_url = f"https://tenipo.com/match/-/{match_id}"
        try:
            self.driver.get(match_page_url)
            time.sleep(5)

            # Get all intercepted URLs
            get_urls_script = """
                return Object.keys(window.interceptedResponses || {});
            """
            urls = self.driver.execute_script(get_urls_script)
            logging.info(f"INVESTIGATION: Found {len(urls)} intercepted URLs for match {match_id}: {urls}")
            return urls
        except Exception as e:
            logging.error(f"Error during investigation for match {match_id}: {e}")
            return []