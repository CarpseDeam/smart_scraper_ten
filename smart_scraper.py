import logging
import base64
import zlib
import gzip
import xml.etree.ElementTree as ET
import httpx
from typing import Optional

import config


class TenipoClient:
    """Manages fetching and decoding data from the Tenipo API endpoints directly via HTTP."""

    def __init__(self, settings: config.Settings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            headers={"User-Agent": self.settings.USER_AGENT},
            timeout=30.0
        )
        logging.info("TenipoClient initialized with HTTPX.")

    async def close(self):
        """Closes the HTTP client."""
        await self.client.aclose()
        logging.info("TenipoClient closed.")

    def _decode_payload(self, payload: bytes) -> bytes:
        """
        Decodes the Base64 payload and then attempts every standard decompression method.
        This is a diagnostic approach to find the correct format.
        """
        try:
            base64_decoded = base64.b64decode(payload)
        except Exception as e:
            logging.error(f"CRITICAL: Base64 decoding step failed: {e}")
            return b""

        # Attempt 1: Raw Deflate (for 'incorrect header check' errors)
        try:
            return zlib.decompress(base64_decoded, -zlib.MAX_WBITS)
        except Exception:
            pass  # Suppress error and try next method

        # Attempt 2: Standard zlib
        try:
            return zlib.decompress(base64_decoded)
        except Exception:
            pass  # Suppress error and try next method

        # Attempt 3: Gzip
        try:
            return gzip.decompress(base64_decoded)
        except Exception:
            pass  # Suppress error and try next method

        # If all methods fail, log the diagnostic info.
        logging.error("CRITICAL: All decompression methods failed. The data format is unknown.")
        logging.error(f"DIAGNOSTIC: Raw bytes (first 50) after Base64 decode: {base64_decoded[:50].hex()}")
        return b""

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

    async def get_live_matches_summary(self) -> list[dict]:
        """Fetches the summary data for all live matches directly via HTTP."""
        try:
            logging.info(f"Fetching live matches summary from {self.settings.LIVE_FEED_DATA_URL}")
            response = await self.client.get(self.settings.LIVE_FEED_DATA_URL)
            response.raise_for_status()

            encrypted_content_bytes = response.content
            decompressed_xml_bytes = self._decode_payload(encrypted_content_bytes)

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
        """Fetches detailed data for a single match directly via HTTP."""
        match_xml_full_url = self.settings.MATCH_XML_URL_TEMPLATE.format(match_id=match_id)
        try:
            logging.info(f"Fetching data for match from: {match_xml_full_url}")
            response = await self.client.get(match_xml_full_url)
            response.raise_for_status()

            encrypted_body_bytes = response.content
            if not encrypted_body_bytes:
                logging.warning(f"Response body for match {match_id} was empty.")
                return {}

            decompressed_xml_bytes = self._decode_payload(encrypted_body_bytes)
            if not decompressed_xml_bytes:
                raise ValueError(f"Payload decoding returned empty result for match {match_id}")

            root = ET.fromstring(decompressed_xml_bytes)
            logging.info(f"Successfully fetched and decoded data for match {match_id}")
            return self._xml_to_dict(root)
        except Exception as e:
            logging.error(f"An unexpected error occurred in fetch_match_data for ID {match_id}: {e}", exc_info=True)
            return {}