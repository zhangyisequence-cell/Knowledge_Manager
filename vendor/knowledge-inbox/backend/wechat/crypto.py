"""Authenticated WeChat callbacks following the official AES-256-CBC protocol.

The timestamp is authenticated but freshness/replay checks and sender authorization
belong to the callback/queue layer. This module performs no network or disk I/O.
"""

import base64
import binascii
import hashlib
import hmac
import re
from xml.etree.ElementTree import ParseError

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

DEFAULT_MAX_PAYLOAD_BYTES = 64 * 1024
MAX_ALLOWED_BYTES = 1024 * 1024
MAX_XML_FIELDS = 64


class CallbackCryptoError(ValueError):
    """Invalid callback/configuration; error text never includes secret inputs."""


def _byte_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= MAX_ALLOWED_BYTES:
        raise CallbackCryptoError("Invalid callback byte limit")
    return value


def _ascii(value: str, max_length: int) -> bytes:
    if not isinstance(value, str) or not 1 <= len(value) <= max_length:
        raise CallbackCryptoError("Invalid callback parameter")
    try:
        return value.encode("ascii")
    except UnicodeEncodeError:
        raise CallbackCryptoError("Invalid callback parameter") from None


def parse_callback_xml(
    xml: bytes | str, *, max_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
) -> dict[str, str]:
    """Parse a bounded, flat <xml> callback, rejecting DTDs and ambiguous fields.

    This parses data only; it does not authenticate an outer envelope. Never trust
    its sender fields until `WeChatCallbackCrypto.decrypt_xml` has authenticated it.
    Nested message formats intentionally require a separate, explicit schema.
    """
    _byte_limit(max_bytes)
    if not isinstance(xml, (bytes, str)) or not 0 < len(xml) <= max_bytes:
        raise CallbackCryptoError("Invalid callback XML")
    try:
        raw = xml.encode("utf-8") if isinstance(xml, str) else xml
        if len(raw) > max_bytes:
            raise CallbackCryptoError("Invalid callback XML")
        root = ElementTree.fromstring(
            raw, forbid_dtd=True, forbid_entities=True, forbid_external=True
        )
    except (UnicodeError, ParseError, DefusedXmlException, ValueError, LookupError):
        raise CallbackCryptoError("Invalid callback XML") from None
    if root.tag != "xml" or root.attrib or (root.text or "").strip():
        raise CallbackCryptoError("Invalid callback XML")
    if len(root) > MAX_XML_FIELDS:
        raise CallbackCryptoError("Invalid callback XML")
    fields = {}
    for field in root:
        if (
            not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", field.tag)
            or field.tag in fields
            or field.attrib
            or len(field)
            or (field.tail or "").strip()
        ):
            raise CallbackCryptoError("Invalid callback XML")
        fields[field.tag] = field.text or ""
    return fields


def extract_encrypted(xml: bytes | str, *, max_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES) -> str:
    """Extract exactly one nonempty Encrypt field; the value is not yet trusted."""
    encrypted = parse_callback_xml(xml, max_bytes=max_bytes).get("Encrypt")
    if not encrypted:
        raise CallbackCryptoError("Invalid callback XML")
    return encrypted


class WeChatCallbackCrypto:
    """Verify/decrypt callbacks for one explicitly configured, nonempty receiver ID."""

    def __init__(
        self,
        token: str,
        encoding_aes_key: str,
        receiver_id: str,
        *,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    ):
        self._token = _ascii(token, 256)
        self._receiver = _ascii(receiver_id, 256)
        self._max_payload_bytes = _byte_limit(max_payload_bytes)
        if not isinstance(encoding_aes_key, str) or not re.fullmatch(
            r"[A-Za-z0-9+/]{43}", encoding_aes_key
        ):
            raise CallbackCryptoError("Invalid EncodingAESKey")
        try:
            self._key = base64.b64decode(encoding_aes_key + "=", validate=True)
        except (ValueError, binascii.Error):
            raise CallbackCryptoError("Invalid EncodingAESKey") from None
        if len(self._key) != 32:
            raise CallbackCryptoError("Invalid EncodingAESKey")
        # WeChat padding is to 32 bytes, despite AES's 16-byte block size.
        frame_limit = 20 + max_payload_bytes + len(self._receiver)
        self._max_cipher_bytes = (frame_limit // 32 + 1) * 32
        self._max_base64_chars = 4 * ((self._max_cipher_bytes + 2) // 3)

    def decrypt(self, encrypted: str, signature: str, timestamp: str, nonce: str) -> bytes:
        """Authenticate the entire ciphertext before decryption and return message bytes."""
        encrypted_bytes = _ascii(encrypted, self._max_base64_chars)
        timestamp_bytes = _ascii(timestamp, 128)
        nonce_bytes = _ascii(nonce, 128)
        if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{40}", signature):
            raise CallbackCryptoError("Invalid callback")
        expected = hashlib.sha1(
            b"".join(sorted([self._token, timestamp_bytes, nonce_bytes, encrypted_bytes]))
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise CallbackCryptoError("Invalid callback")
        try:
            ciphertext = base64.b64decode(encrypted_bytes, validate=True)
        except (ValueError, binascii.Error):
            raise CallbackCryptoError("Invalid callback") from None
        if not ciphertext or len(ciphertext) % 32 or len(ciphertext) > self._max_cipher_bytes:
            raise CallbackCryptoError("Invalid callback")
        try:
            decryptor = Cipher(algorithms.AES(self._key), modes.CBC(self._key[:16])).decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            unpadder = padding.PKCS7(256).unpadder()
            frame = unpadder.update(padded) + unpadder.finalize()
        except ValueError:
            raise CallbackCryptoError("Invalid callback") from None
        if len(frame) < 20:
            raise CallbackCryptoError("Invalid callback")
        message_length = int.from_bytes(frame[16:20], "big")
        end = 20 + message_length
        if message_length > self._max_payload_bytes or end > len(frame):
            raise CallbackCryptoError("Invalid callback")
        if not hmac.compare_digest(frame[end:], self._receiver):
            raise CallbackCryptoError("Invalid callback")
        return frame[20:end]

    def decrypt_challenge(self, encrypted: str, signature: str, timestamp: str, nonce: str) -> str:
        """Return the GET echostr plaintext exactly, without XML parsing or wrapping."""
        message = self.decrypt(encrypted, signature, timestamp, nonce)
        try:
            return message.decode("utf-8")
        except UnicodeDecodeError:
            raise CallbackCryptoError("Invalid callback") from None

    def decrypt_xml(
        self, envelope: bytes | str, signature: str, timestamp: str, nonce: str
    ) -> dict[str, str]:
        """Read the envelope Encrypt field and return authenticated flat message fields."""
        envelope_limit = min(self._max_base64_chars + 4096, MAX_ALLOWED_BYTES)
        encrypted = extract_encrypted(envelope, max_bytes=envelope_limit)
        message = self.decrypt(encrypted, signature, timestamp, nonce)
        return parse_callback_xml(message, max_bytes=self._max_payload_bytes)
