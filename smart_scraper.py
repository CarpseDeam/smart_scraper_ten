import logging
import base64
import xml.etree.ElementTree as ET
import httpx
from typing import List, Dict, Any

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class TenipoClient:
    """
    A high-performance, asynchronous HTTP client for fetching and decoding data from Tenipo.
    This client mimics a real browser's headers and is robust against malformed server responses.
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
        self.client = httpx.AsyncClient(headers=self.DEFAULT_HEADERS, timeout=30.0)
        logging.info("TenipoClient initialized with httpx and stealth headers.")

    async def close(self):
        await self.client.aclose()
        logging.info("TenipoClient httpx session closed.")

    def _decode_payload(self, payload: bytes) -> bytes:
        """
        The reverse-engineered multi-step decoding function for Tenipo's data payloads.
        Now includes defensive padding for malformed Base64 strings.
        """
        # Guard clause for empty payloads from the server
        if not payload:
            return b""

        try:
            decoded_b64_bytes = base64.b64decode(payload)
            data_len = len(decoded_b64_bytes)
            if data_len == 0: return b""

            char_list = []
            for i, byte_val in enumerate(decoded_b64_bytes):
                shift = (i % data_len - i % 4) * data_len + 64
                new_char_code = (byte_val - shift) % 256
                char_list.append(chr(new_char_code))

            second_base64_string = "".join(char_list)

            # ===================================================================
            # ===> THE PADDING FIX: Make the string valid before decoding <===
            # Some server responses are missing the Base64 padding. We add it back.
            missing_padding = len(second_base64_string) % 4
            if missing_padding:
                second_base64_string += '=' * (4 - missing_padding)
            # ===================================================================

            final_xml_bytes = base64.b64decode(second_base64_string.encode('latin-1'))
            return final_xml_bytes
        except Exception as e:
            # This will catch both decoding and padding errors for junk payloads.
            logging.error(f"Payload decoding failed, likely a junk payload from server. Error: {e}")
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
            response = await self.client.get(self.settings.LIVE_FEED_DATA_URL)
            response.raise_for_status()
            decompressed_xml_bytes = self._decode_payload(response.content)
            if not decompressed_xml_bytes:
                # This is now an expected outcome for junk payloads, so we just log and continue.
                logging.info("Payload was empty or junk after decoding. Skipping.")
                return []
            root = ET.fromstring(decompressed_xml_bytes)
            return [self._xml_to_dict(tag) for tag in root.findall("./match")]
        except ET.ParseError:
            # This was our other error. It means the padding fix worked, but the XML is still funky.
            # This is a rare edge case, but we handle it gracefully.
            logging.warning("XML was not well-formed after decoding. Server may have sent a non-XML payload.")
            return []
        except Exception as e:
            logging.error(f"Error in get_live_matches_summary: {e}")
            return []

    async def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        """Fetches and decodes detailed data for a single match."""
        match_xml_full_url = self.settings.MATCH_XML_URL_TEMPLATE.format(match_id=match_id)
        try:
            response = await self.client.get(match_xml_full_url)
            response.raise_for_status()
            decompressed_xml_bytes = self._decode_payload(response.content)
            if not decompressed_xml_bytes:
                logging.info(f"Payload for match {match_id} was empty or junk. Skipping.")
                return {}
            root = ET.fromstring(decompressed_xml_bytes)
            return self._xml_to_dict(root)
        except Exception as e:
            logging.error(f"Error in fetch_match_data for ID {match_id}: {e}")
            return {}