import logging
import base64
from lxml import etree as ET
import httpx
from typing import List, Dict, Any

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# The class name is now CORRECTLY TenipoScraper to match what main.py imports.
class TenipoScraper:
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
        logging.info("TenipoScraper (HTTPX) initialized with stealth headers.")

    async def close(self):
        await self.client.aclose()
        logging.info("TenipoScraper httpx session closed.")

    def _decode_payload(self, payload: bytes) -> bytes:
        if not payload: return b""
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
            missing_padding = len(second_base64_string) % 4
            if missing_padding:
                second_base64_string += '=' * (4 - missing_padding)
            final_xml_bytes = base64.b64decode(second_base64_string.encode('latin-1'))
            return final_xml_bytes
        except Exception:
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

    async def get_live_matches_summary(self) -> List[Dict[str, Any]]:
        try:
            response = await self.client.get(self.settings.LIVE_FEED_DATA_URL)
            response.raise_for_status()
            decompressed_xml_bytes = self._decode_payload(response.content)
            if not decompressed_xml_bytes:
                logging.info("Payload was empty or junk after decoding. Skipping.")
                return []
            parser = ET.XMLParser(recover=True)
            root = ET.fromstring(decompressed_xml_bytes, parser=parser)
            if root is None:
                logging.warning("XML was unrecoverably broken after decoding. Skipping payload.")
                return []
            match_tags = root.findall("./match")
            if not match_tags:
                match_tags = root.findall("./event")
            return [self._xml_to_dict(tag) for tag in match_tags]
        except Exception as e:
            logging.error(f"Error in get_live_matches_summary: {e}")
            return []

    async def fetch_match_data(self, match_id: str) -> Dict[str, Any]:
        match_xml_full_url = self.settings.MATCH_XML_URL_TEMPLATE.format(match_id=match_id)
        try:
            response = await self.client.get(match_xml_full_url)
            response.raise_for_status()
            decompressed_xml_bytes = self._decode_payload(response.content)
            if not decompressed_xml_bytes:
                logging.info(f"Payload for match {match_id} was empty or junk. Skipping.")
                return {}
            parser = ET.XMLParser(recover=True)
            root = ET.fromstring(decompressed_xml_bytes, parser=parser)
            if root is None:
                logging.warning(f"XML for match {match_id} was unrecoverably broken. Skipping.")
                return {}
            return self._xml_to_dict(root)
        except Exception as e:
            logging.error(f"Error in fetch_match_data for ID {match_id}: {e}")
            return {}