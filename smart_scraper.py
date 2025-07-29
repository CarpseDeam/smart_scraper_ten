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
        logging.info("TenipoScraper instance created. Call start() to launch the browser.")

    async def start(self):
        """Launches the browser."""
        logging.info("Starting Playwright...")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
        logging.info("Playwright browser initialized successfully.")

    async def close(self):
        """Closes the browser and stops Playwright."""
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logging.info("Playwright browser and context closed.")

    async def _get_page_with_decoder(self) -> Page:
        """Gets a fresh page and navigates to it, ensuring the decoder function is loaded."""
        if not self.browser:
            raise ConnectionError("Browser is not available.")
        page = await self.browser.new_page(user_agent=self.settings.USER_AGENT)
        await page.goto(str(self.settings.LIVESCORE_PAGE_URL), timeout=60000, wait_until="domcontentloaded")
        # Wait for the specific janko function to be defined on the page.
        await page.wait_for_function("() => typeof janko === 'function'", timeout=15000)
        logging.info("Page loaded and 'janko' decoder function is ready.")
        return page

    async def _decode_payload(self, page: Page, payload: bytes) -> bytes:
        """Delegates decoding to the browser's native JavaScript engine."""
        try:
            base64_string = payload.decode('ascii')
            # Pass the function as an argument to evaluate to avoid scope issues
            decoded_xml_string = await page.evaluate("janko => janko(arguments[0])", base64_string)
            if not decoded_xml_string or not decoded_xml_string.strip().startswith('<'):
                logging.error(f"JavaScript decoding failed. Result: {decoded_xml_string[:200]}")
                return b""
            return decoded_xml_string.encode('utf-8')
        except Exception as e:
            logging.error(f"DECODING FAILED during JS execution: {e}", exc_info=True)
            return b""

    async def get_live_matches_summary(self) -> list[dict]:
        """Fetches the summary data for all live matches."""
        page = None
        try:
            page = await self._get_page_with_decoder()
            logging.info("Fetching live matches summary...")

            async with page.expect_response(
                    lambda response: self.settings.LIVE_FEED_DATA_URL in response.url,
                    timeout=30000
            ) as response_info:
                # Reloading now is safe because the context is established
                await page.reload(wait_until="networkidle", timeout=30000)

            response = await response_info.value
            encrypted_content_bytes = await response.body()

            logging.info(f"Intercepted live feed: {response.url}. Now decoding.")
            decompressed_xml_bytes = await self._decode_payload(page, encrypted_content_bytes)

            if not decompressed_xml_bytes:
                raise ValueError("Payload decoding returned empty result.")

            root = ET.fromstring(decompressed_xml_bytes)
            live_matches = [self._xml_to_dict(match_tag) for match_tag in root.findall("./match")]
            logging.info(f"Found {len(live_matches)} total live matches in summary.")
            return live_matches
        except Exception as e:
            logging.error(f"An error occurred in get_live_matches_summary: {e}", exc_info=True)
            return []
        finally:
            if page: await page.close()

    async def fetch_match_data(self, match_id: str) -> dict:
        """Fetches detailed data for a single match."""
        page = None
        match_xml_full_url = self.settings.MATCH_XML_URL_TEMPLATE.format(match_id=match_id)
        try:
            page = await self._get_page_with_decoder()
            logging.info(f"Fetching data for match: {match_xml_full_url}")

            async with page.expect_response(match_xml_full_url, timeout=30000) as response_info:
                # Trigger the fetch from within the page's JS context
                await page.evaluate(f"url => fetch(url)", match_xml_full_url)

            response = await response_info.value
            encrypted_body_bytes = await response.body()

            if not encrypted_body_bytes:
                logging.warning(f"Response body for match {match_id} was empty.")
                return {}

            decompressed_xml_bytes = await self._decode_payload(page, encrypted_body_bytes)
            if not decompressed_xml_bytes:
                raise ValueError(f"Payload decoding returned empty result for match {match_id}")

            root = ET.fromstring(decompressed_xml_bytes)
            logging.info(f"Successfully fetched and decoded data for match {match_id}")
            return self._xml_to_dict(root)
        except Exception as e:
            logging.error(f"An error occurred in fetch_match_data for ID {match_id}: {e}", exc_info=True)
            return {}
        finally:
            if page: await page.close()

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