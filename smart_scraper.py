import logging
import base64
import xml.etree.ElementTree as ET
import httpx
from typing import Optional

import config


class TenipoClient:
    """Manages fetching data and running it through the custom Janko decoder cipher."""

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
        Implements the custom multi-step decoding process reverse-engineered from the website's janko() function.
        """
        try:
            # First Pass Decode
            first_pass_decoded_bytes = base64.b64decode(payload)

            # The Custom Cipher Loop
            data_len = len(first_pass_decoded_bytes)
            char_list = []

            for i, byte_val in enumerate(first_pass_decoded_bytes):
                # Calculate the shift value using the exact mathematical formula.
                shift = (i % data_len - i % 4) * data_len + 64

                # Calculate the new character's code.
                new_char_code = byte_val - shift

                # Convert this new_char_code back into a character and append.
                char_list.append(chr(new_char_code))

            # Join the Characters into the second Base64 string.
            second_base64_string = "".join(char_list)

            # Second Pass Decode to get the final, clean data.
            final_xml_bytes = base64.b64decode(second_base64_string)

            return final_xml_bytes
        except Exception as e:
            # If any part of the decoding fails, log the error and return empty bytes.
            logging.error(f"Custom payload decoding failed: {e}", exc_info=True)
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
                return {}

            decompressed_xml_bytes = self._decode_payload(encrypted_body_bytes)
            if not decompressed_xml_bytes:
                raise ValueError(f"Payload decoding returned empty result for match {match_id}")

            root = ET.fromstring(decompressed_xml_bytes)
            return self._xml_to_dict(root)
        except Exception as e:
            logging.error(f"An unexpected error occurred in fetch_match_data for ID {match_id}: {e}", exc_info=True)
            return {}
