"""Protocol fixtures are public/synthetic; never use these values in a real account."""

import hashlib

import pytest
from backend.wechat import crypto as wechat_crypto

# Official vector: https://developer.work.weixin.qq.com/document/path/90968
TOKEN = "QDG6eK"
KEY = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C"
RECEIVER = "wx5823bf96d3bd56c7"
TIMESTAMP = "1409659813"
NONCE = "1372623149"
OFFICIAL_SIGNATURE = "477715d11cdb4164915debcba66cb864d751f3e6"
OFFICIAL_ENCRYPTED = (
    "RypEvHKD8QQKFhvQ6QleEB4J58tiPdvo+rtK1I9qca6aM/wvqnLSV5zEPeusUiX5L5X/"
    "0lWfrf0QADHHhGd3QczcdCUpj911L3vg3W/sYYvuJTs3TUUkSUXxaccAS0qhxchrRYt66wi"
    "SpGLYL42aM6A8dTT+6k4aSknmPj48kzJs8qLjvd4Xgpue06DOdnLxAUHzM6+kDZ+HMZfJ"
    "YuR+LtwGc2hgf5gsijff0ekUNXZiqATP7PF5mZxZ3Izoun1s4zG4LUMnvw2r+KqCKIw+3IQ"
    "H03v+BCA9nMELNqbSf6tiWSrXJB3LAVGUcallcrw8V2t9EL4EhzJWrQUax5wLVMNS0+rUP"
    "A3k22Ncx4XXZS9o0MBH27Bo6BpNelZpS+/uh9KsNlY6bHCmJU9p8g7m3fVKn28H3KDYA5"
    "Pl/T8Z1ptDAVe0lXdQ2YoyyH2uyPIGHBZZIs2pDBS8R07+qN+E7Q=="
)

# Independently generated with .NET Aes/CBC/Padding=None, not the code under test.
# Packet: bytes(range(16)) + big-endian length + UTF-8 message + RECEIVER;
# explicitly append PKCS#7 padding to a 32-byte boundary before .NET encryption.
CHALLENGE = (
    "JVwoIzeb5XuqKIT2XqjnoBUYPJJT+gkJOhnNDCUnwKgt86m4GhA2ofsF9+8eeIrb"
    "YDbE3fHjCtVb6haxOruLgA=="
)
CHALLENGE_SIGNATURE = "7b80f24ecc132dfe7d98cb7695c9f849f43453be"
INVALID_PACKETS = [
    # A zero pad byte; padding must be in 1..32.
    (
        (
            "JVwoIzeb5XuqKIT2XqjnoBUYPJJT+gkJOhnNDCUnwKgt86m4GhA2ofsF9+8eeIrb"
            "mJXBjxM8KwMEIY8ZDyepEA=="
        ),
        "3791a145392b1468255706a5e3713996a3317552",
    ),
    # Inconsistent final padding bytes (01 12).
    (
        (
            "JVwoIzeb5XuqKIT2XqjnoBUYPJJT+gkJOhnNDCUnwKgt86m4GhA2ofsF9+8eeIrb"
            "9nZBHk6eANVMVsATcKtqKQ=="
        ),
        "8f824124ca85124a9b342446fe6af4d6f91e1f6f",
    ),
    # Valid padding but only ten unpadded bytes, shorter than the packet header.
    (
        "5OxkwRG2NvHmmJxmK0zZBtkF3KY52VJcH7aMIH/TPzA=",
        "e99894c96e8c12b1a39c67353ff35100adff8481",
    ),
    # Declared message length 0xffffffff extends beyond the decrypted packet.
    (
        (
            "JVwoIzeb5XuqKIT2XqjnoLusXfSGwlzn2GJW8Oe0DNEBPFdkXQiJA7psZvdo33Ah"
            "+7sexWKSySuWHbFNTXRAgQ=="
        ),
        "c8e3c99128a7660391e0434b5f1f01af5c1dca2b",
    ),
]


@pytest.fixture
def crypto_module():
    return wechat_crypto


@pytest.fixture
def crypto(crypto_module):
    return crypto_module.WeChatCallbackCrypto(TOKEN, KEY, RECEIVER)


def signed(encrypted):
    """Only for malformed transport inputs; positive signatures are static fixtures."""
    return hashlib.sha1(
        "".join(sorted([TOKEN, TIMESTAMP, NONCE, encrypted])).encode()
    ).hexdigest()


def test_official_callback_vector_verifies_and_decrypts(crypto, crypto_module):
    message = crypto.decrypt(OFFICIAL_ENCRYPTED, OFFICIAL_SIGNATURE, TIMESTAMP, NONCE)
    assert crypto_module.parse_callback_xml(message) == {
        "ToUserName": RECEIVER,
        "FromUserName": "mycreate",
        "CreateTime": "1409659813",
        "MsgType": "text",
        "Content": "hello",
        "MsgId": "4561255354251345929",
        "AgentID": "218",
    }


def test_url_challenge_returns_exact_plain_text_without_xml_parsing(crypto):
    assert (
        crypto.decrypt_challenge(CHALLENGE, CHALLENGE_SIGNATURE, TIMESTAMP, NONCE)
        == "1234567890"
    )


def test_32_byte_padding_is_supported(crypto):
    encrypted = (
        "JVwoIzeb5XuqKIT2XqjnoN8BPomycJWFjC+MMnjLbuKigACZBvTkDUVZVpsCKoFQ"
        "DIRIaUsHrGyL00V2JGm9HJArLqx4iYx/DAx2Wf+JhTS5JPpgWI8m+IXvtm3OhR/B"
    )
    assert (
        crypto.decrypt(
            encrypted, "9540add639d71a5c9e43c7fd9e3021f2c4a1bf4e", TIMESTAMP, NONCE
        )
        == b"A" * 26
    )


@pytest.mark.parametrize("field", ["signature", "timestamp", "nonce", "encrypted"])
def test_any_signed_input_tampering_is_rejected(crypto, crypto_module, field):
    params = {
        "encrypted": CHALLENGE,
        "signature": CHALLENGE_SIGNATURE,
        "timestamp": TIMESTAMP,
        "nonce": NONCE,
    }
    params[field] = "0" + params[field][1:]
    with pytest.raises(crypto_module.CallbackCryptoError):
        crypto.decrypt(**params)


@pytest.mark.parametrize(
    "signature", ["", "z" * 40, "a" * 39, "é" * 40, CHALLENGE_SIGNATURE.upper()]
)
def test_malformed_signature_is_rejected(crypto, crypto_module, signature):
    with pytest.raises(crypto_module.CallbackCryptoError):
        crypto.decrypt(CHALLENGE, signature, TIMESTAMP, NONCE)


@pytest.mark.parametrize("key", ["", KEY[:-1], KEY + "=", "!" * 43, "é" * 43])
def test_bad_encoding_aes_key_cannot_be_configured(crypto_module, key):
    with pytest.raises(crypto_module.CallbackCryptoError):
        crypto_module.WeChatCallbackCrypto(TOKEN, key, RECEIVER)


@pytest.mark.parametrize(
    "token,receiver", [("", RECEIVER), (TOKEN, ""), ("a" * 257, RECEIVER)]
)
def test_missing_or_unbounded_configuration_is_rejected(crypto_module, token, receiver):
    with pytest.raises(crypto_module.CallbackCryptoError):
        crypto_module.WeChatCallbackCrypto(token, KEY, receiver)


def test_receiver_identity_must_match_exactly(crypto_module):
    wrong = crypto_module.WeChatCallbackCrypto(TOKEN, KEY, RECEIVER[:-1])
    with pytest.raises(crypto_module.CallbackCryptoError):
        wrong.decrypt(CHALLENGE, CHALLENGE_SIGNATURE, TIMESTAMP, NONCE)


@pytest.mark.parametrize(
    "encrypted", ["", "not-base64!", "YQ==", "AA==" * 16, "A" * 44, CHALLENGE + "\n"]
)
def test_invalid_base64_and_ciphertext_lengths_are_rejected(
    crypto, crypto_module, encrypted
):
    with pytest.raises(crypto_module.CallbackCryptoError):
        crypto.decrypt(encrypted, signed(encrypted), TIMESTAMP, NONCE)


@pytest.mark.parametrize("encrypted,signature", INVALID_PACKETS)
def test_invalid_padding_and_message_frame_are_rejected(
    crypto, crypto_module, encrypted, signature
):
    with pytest.raises(crypto_module.CallbackCryptoError):
        crypto.decrypt(encrypted, signature, TIMESTAMP, NONCE)


def test_challenge_with_invalid_utf8_is_rejected(crypto, crypto_module):
    encrypted = (
        "JVwoIzeb5XuqKIT2XqjnoObwkKns0MyEsbdhNoVDCGqsNRvMru0hZHyFr+Zk/3Wy"
        "gN2aC40EOZPbRnEKsEovew=="
    )
    with pytest.raises(crypto_module.CallbackCryptoError):
        crypto.decrypt_challenge(
            encrypted, "b791f25cea8a810be3e599addaa9b4e2fa2c333f", TIMESTAMP, NONCE
        )


def test_payload_size_limit_is_enforced(crypto_module):
    tiny = crypto_module.WeChatCallbackCrypto(TOKEN, KEY, RECEIVER, max_payload_bytes=9)
    with pytest.raises(crypto_module.CallbackCryptoError):
        tiny.decrypt(CHALLENGE, CHALLENGE_SIGNATURE, TIMESTAMP, NONCE)
    exact = crypto_module.WeChatCallbackCrypto(
        TOKEN, KEY, RECEIVER, max_payload_bytes=10
    )
    assert (
        exact.decrypt(CHALLENGE, CHALLENGE_SIGNATURE, TIMESTAMP, NONCE) == b"1234567890"
    )


def test_envelope_decryption_extracts_authenticated_xml(crypto):
    envelope = f"<xml><ToUserName>{RECEIVER}</ToUserName><Encrypt><![CDATA[{OFFICIAL_ENCRYPTED}]]></Encrypt></xml>"
    assert (
        crypto.decrypt_xml(envelope, OFFICIAL_SIGNATURE, TIMESTAMP, NONCE)["Content"]
        == "hello"
    )


def test_xml_extraction_preserves_unicode_and_cdata(crypto_module):
    assert crypto_module.parse_callback_xml(
        "<xml><Content><![CDATA[知识 & 提取]]></Content></xml>"
    ) == {"Content": "知识 & 提取"}
    assert (
        crypto_module.extract_encrypted(f"<xml><Encrypt>{CHALLENGE}</Encrypt></xml>")
        == CHALLENGE
    )


@pytest.mark.parametrize(
    "xml",
    [
        "<!DOCTYPE xml><xml><Encrypt>x</Encrypt></xml>",
        '<!DOCTYPE xml [<!ENTITY e "expanded">]><xml><Encrypt>&e;</Encrypt></xml>',
        '<!DOCTYPE xml SYSTEM "file:///not-read"><xml/>',
        "<xml><Encrypt>a</Encrypt><Encrypt>b</Encrypt></xml>",
        "<xml><Encrypt><Nested>x</Nested></Encrypt></xml>",
        '<xml><Encrypt version="1">x</Encrypt></xml>',
        "<wrong><Encrypt>x</Encrypt></wrong>",
        "<xml>unexpected<Encrypt>x</Encrypt></xml>",
        "<xml><Encrypt>x</Encrypt>unexpected</xml>",
        "<xml><Encrypt>",
        b'<?xml version="1.0" encoding="unknown-encoding"?><xml/>',
    ],
)
def test_unsafe_or_ambiguous_xml_is_rejected(crypto_module, xml):
    with pytest.raises(crypto_module.CallbackCryptoError):
        crypto_module.parse_callback_xml(xml)


@pytest.mark.parametrize("xml", ["<xml/>", "<xml><Encrypt/></xml>"])
def test_missing_or_empty_encrypted_field_is_rejected(crypto_module, xml):
    with pytest.raises(crypto_module.CallbackCryptoError):
        crypto_module.extract_encrypted(xml)


def test_xml_size_and_field_count_are_bounded(crypto_module):
    with pytest.raises(crypto_module.CallbackCryptoError):
        crypto_module.parse_callback_xml(
            "<xml><Content>知识</Content></xml>", max_bytes=30
        )
    with pytest.raises(crypto_module.CallbackCryptoError):
        crypto_module.parse_callback_xml(
            "<xml>" + "".join(f"<F{i}/>" for i in range(65)) + "</xml>"
        )
