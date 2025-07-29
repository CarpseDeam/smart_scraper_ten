import logging
import json
import xml.etree.ElementTree as ET
from typing import Optional

import config
from playwright.async_api import async_playwright, Browser, Page, Playwright


class TenipoScraper:
    """Manages the browser instance and orchestrates the scraping process using Playwright."""

    def __init__(self, settings: config.Settings):
        self.settings = settings
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.decoder_script_content: Optional[str] = None
        logging.info("TenipoScraper instance created. Call start() to launch the browser.")

    async def start(self):
        """Launches the browser and finds the essential decoder script."""
        logging.info("Starting Playwright...")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        page = await self.browser.new_page()
        try:
            logging.info(f"Navigating to {self.settings.LIVESCORE_PAGE_URL} to find decoder script...")
            await page.goto(str(self.settings.LIVESCORE_PAGE_URL), timeout=60000)
            # Find the specific script tag containing the 'janko' function. This is the key.
            script_locator = page.locator("script:has-text('function janko')")
            await script_locator.wait_for(timeout=15000)
            self.decoder_script_content = await script_locator.inner_html()
            if not self.decoder_script_content:
                raise RuntimeError("Could not extract the janko decoder script from the page.")
            logging.info("Successfully extracted the 'janko' decoder script.")
        finally:
            await page.close()

    async def close(self):
        """Closes the browser and stops Playwright."""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logging.info("Playwright browser and context closed.")

    async def _execute_request_and_decode(self, url: str) -> bytes:
        """Opens a new page, injects the decoder, runs the request, and decodes the result."""
        if not self.browser or not self.decoder_script_content:
            raise ConnectionError("Scraper not started or decoder script not found.")

        page = await self.browser.new_page(user_agent=self.settings.USER_AGENT)
        try:
            # Inject our saved decoder script into the new page. This is the robust fix.
            await page.add_script_tag(content=self.decoder_script_content)

            # Now, perform the actual data fetch.
            async with page.expect_response(lambda r: url in r.url, timeout=30000) as response_info:
                # This JS evaulation triggers the fetch from within the browser context.
                await page.evaluate("url => fetch(url)", url)

            response = await response_info.value
            encoded_body = await response.body()

            if not encoded_body:
                return b""

            # Decode the payload using the injected 'janko' function.
            base64_string = encoded_body.decode('ascii')
            decoded_xml_string = await page.evaluate("janko => janko(arguments[0])", base64_string)

            return decoded_xml_string.encode('utf-8')
        finally:
            await page.close()

    async def get_live_matches_summary(self) -> list[dict]:
        """Fetches the summary data for all live matches."""
        try:
            logging.info("Fetching live matches summary...")
            decompressed_xml_bytes = await self._execute_request_and_decode(self.settings.LIVE_FEED_DATA_URL)
            if not decompressed_xml_bytes:
                raise ValueError("Payload decoding returned empty result.")

            root = ET.fromstring(decompressed_xml_bytes)
            live_matches = [self._xml_to_dict(match_tag) for match_tag in root.findall("./match")]
            logging.info(f"Found {len(live_matches)} total live matches in summary.")
            return live_matches
        except Exception as e:
            logging.error(f"An error occurred in get_live_matches_summary: {e}", exc_info=True)
            return []

    async def fetch_match_data(self, match_id: str) -> dict:
        """Fetches detailed data for a single match."""
        match_xml_full_url = self.settings.MATCH_XML_URL_TEMPLATE.format(match_id=match_id)
        try:
            logging.info(f"Fetching data for match: {match_xml_full_url}")
            decompressed_xml_bytes = await self._execute_request_and_decode(match_xml_full_url)
            if not decompressed_xml_bytes:
                logging.warning(f"Response body for match {match_id} was empty.")
                return {}

            root = ET.fromstring(decompressed_xml_bytes)
            logging.info(f"Successfully fetched and decoded data for match {match_id}")
            return self._xml_to_dict(root)
        except Exception as e:
            logging.error(f"An error occurred in fetch_match_data for ID {match_id}: {e}", exc_info=True)
            return {}

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