# smart_scraper.py
import logging
import re
import time
from typing import List, Dict, Any

import config
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

            # The interception script is still needed for the DETAIL page (match.xml)
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
        if self.driver is None:
            return False, []
        try:
            self.driver.get(str(self.settings.LIVESCORE_PAGE_URL))
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//tr[contains(@id, 'match')]"))
            )
            logging.info("Main livescore page loaded. Parsing HTML for scores...")

            page_source = self.driver.page_source
            tree = html.fromstring(page_source)
            match_rows = tree.xpath("//tr[contains(@id, 'match')]")

            all_matches_data = []
            for row in match_rows:
                # Use the ID of the first player row to get the match ID
                player1_row = row
                player2_row = row.getnext()
                if player2_row is None: continue

                match_id_search = re.search(r'match(\d+)', player1_row.get('id', ''))
                if not match_id_search: continue
                match_id = match_id_search.group(1)

                p1_name = player1_row.find_class('player-name')
                p2_name = player2_row.find_class('player-name')
                tournament_element = row.xpath("./preceding-sibling::tr[@class='tournament'][1]//td/a")

                if not p1_name or not p2_name: continue

                # --- PARSE SCORE FROM HTML USING CORRECT MARKERS ---
                sets = []
                for i in range(1, 6):  # Sets 1 through 5
                    p1_set_score_el = tree.get_element_by_id(f'set1{i}1[{match_id}]', None)
                    p2_set_score_el = tree.get_element_by_id(f'set2{i}1[{match_id}]', None)

                    if p1_set_score_el is not None and p2_set_score_el is not None:
                        sets.append({
                            "p1": p1_set_score_el.text_content().strip(),
                            "p2": p2_set_score_el.text_content().strip()
                        })
                    else:
                        break  # No more sets found

                p1_game_score_el = tree.get_element_by_id(f'game11[{match_id}]', None)
                p2_game_score_el = tree.get_element_by_id(f'game21[{match_id}]', None)

                summary = {
                    "id": match_id,
                    "player1": p1_name[0].text_content().strip(),
                    "player2": p2_name[0].text_content().strip(),
                    "tournament_name": tournament_element[0].text_content().strip() if tournament_element else "N/A",
                    "live_score_data": {
                        "sets": sets,
                        "currentGame": {
                            "p1": p1_game_score_el.text_content().strip() if p1_game_score_el is not None else None,
                            "p2": p2_game_score_el.text_content().strip() if p2_game_score_el is not None else None,
                        }
                    }
                }
                all_matches_data.append(summary)

            logging.info(f"Successfully parsed live scores for {len(all_matches_data)} matches from the main page.")
            return True, all_matches_data

        except Exception as e:
            logging.error(f"Error in get_live_matches_summary: {e}", exc_info=True)
            return False, []

    def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        if self.driver is None: return {}
        match_page_url = f"https://tenipo.com/match/-/{match_id}"
        logging.info(f"FETCHING DETAILS for match ID: {match_id}")
        try:
            self.driver.get(match_page_url)
            # This XML is now only for H2H, round, court, etc. The score is ignored.
            main_xml_str = self._get_intercepted_xml_body(f"match{match_id}.xml", timeout=15)
            if not main_xml_str:
                logging.warning(f"Did not intercept match.xml for details on {match_id}. Some data may be missing.")
                return {"match": {}}  # Return empty to avoid crash

            parser = html.etree.XMLParser(recover=True, encoding='utf-8')
            main_root = html.etree.fromstring(main_xml_str.encode('utf-8'), parser=parser)
            combined_data = {"match": self._xml_to_dict(main_root)}

            combined_data['point_by_point_html'] = self._scrape_html_pbp()
            combined_data['statistics_html'] = self._scrape_html_statistics()
            return combined_data
        except Exception as e:
            logging.error(f"FATAL error in fetch_match_data for ID {match_id}: {e}", exc_info=True)
            return {}

    def _get_intercepted_xml_body(self, url_pattern: str, timeout: int = 15) -> str | None:
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

    def _xml_to_dict(self, element) -> dict:
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
        # This function remains as it targets the detail page
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
            return []  # It's okay if this data doesn't exist.
        except Exception as e:
            logging.error(f"An unexpected error occurred during PBP scraping: {e}", exc_info=True)
            return []

    def _scrape_html_statistics(self) -> List[Dict[str, Any]]:
        # This function also remains as it targets the detail page
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
            logging.error(f"An unexpected error occurred during statistics scraping: {e}", exc_info=True)
        return []