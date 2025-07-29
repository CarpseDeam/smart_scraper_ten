import logging
import base64
import xml.etree.ElementTree as ET
import httpx
from typing import Optional, List, Dict, Any

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class TenipoClient:
    """
    A high-performance, asynchronous HTTP client for fetching and decoding data from Tenipo.
    This client mimics a real browser's headers to avoid being blocked.
    """

    def __init__(self, settings: config.Settings):
        self.settings = settings
        self.DEFAULT_HEADERS = {
            "User-Agent": self.settings.USER_AGENT,
            "Accept": "application/xml, text/xml, */*; q=0.01",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": str(self.settings.LIVESCORE_PAGE_URL)
        }
        self.client = httpx.AsyncClient(
            headers=self.DEFAULT_HEADERS,
            timeout=30.0
        )
        logging.info("TenipoClient initialized with httpx and stealth headers.")

    async def close(self):
        """Closes the HTTP client session gracefully."""
        await self.client.aclose()
        logging.info("TenipoClient httpx session closed.")

    def _decode_payload(self, payload: bytes) -> bytes:
        """
        The reverse-engineered multi-step decoding function for Tenipo's data payloads.
        """
        try:
            decoded_b64_bytes = base64.b64decode(payload)
            data_len = len(decoded_b64_bytes)
            char_list = []
            for i, byte_val in enumerate(decoded_b64_bytes):
                shift = (i % data_len - i % 4) * data_len + 64

                # ===================================================================
                # ===> THE FINAL FIX: Emulate JavaScript's "wrap-around" math <===
                new_char_code = (byte_val - shift) % 256
                # ===================================================================

                char_list.append(chr(new_char_code))

            second_base64_string = "".join(char_list)
            final_xml_bytes = base64.b64decode(second_base64_string)
            return final_xml_bytes
        except Exception as e:
            logging.error(f"Payload decoding failed: {e}", exc_info=True)
            return b""

    def _xml_to_dict(self, element: ET.Element) -> dict:
        """Recursively converts an XML element into a dictionary."""
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

    async def get_live_matches_summary(self) -> List[Dict[str, Any]]:
        """Fetches the summary data for all live matches."""
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
            logging.error(f"An error occurred in get_live_matches_summary: {e}")
            return []

    async def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        """Fetches and decodes detailed data for a single match."""
        match_xml_full_url = self.settings.MATCH_XML_URL_TEMPLATE.format(match_id=match_id)
        try:
            logging.info(f"Fetching data for match from: {match_xml_full_url}")
            response = await self.client.get(match_xml_full_url)
            response.raise_for_status()

            encrypted_body_bytes = response.content
            if not encrypted_body_bytes:
                return {}

            decompressed_xml_bytes = self._decode_payload(encrypted_body_bytes)
            if not decompressed_xml_bytes:
                raise ValueError(f"Payload decoding returned empty result for match {match_id}")

            root = ET.fromstring(decompressed_xml_bytes)
            return self._xml_to_dict(root)
        except Exception as e:
            logging.error(f"An unexpected error occurred in fetch_match_data for ID {match_id}: {e}")
            return {}