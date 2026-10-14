"""Sealing messages for one laptop, and the code people compare when pairing.

A NaCl "Box" combines my private key with your public key. Sealing with it
encrypts a message so only you can read it, and also proves it came from
me: opening it only works with your private key and my public key, and
fails if a single byte was changed on the way.
"""

import base64
import hashlib
import json

from nacl.exceptions import CryptoError
from nacl.public import Box

from connector.core.identity import Identity, decode_public_key


class OpenError(Exception):
    """A sealed message didn't open: not from who it claims, or tampered with."""


def seal(me: Identity, their_key: str, payload: dict) -> str:
    box = Box(me.private_key, decode_public_key(their_key))
    # encrypt() picks a fresh random nonce each time and puts it in front of
    # the result, so sealing the same thing twice gives different output.
    return base64.b64encode(box.encrypt(json.dumps(payload).encode())).decode()


def open_sealed(me: Identity, their_key: str, sealed: str) -> dict:
    try:
        box = Box(me.private_key, decode_public_key(their_key))
        payload = json.loads(box.decrypt(base64.b64decode(sealed, validate=True)))
    except (CryptoError, ValueError, TypeError) as error:
        raise OpenError("the message couldn't be opened") from error
    if not isinstance(payload, dict):
        raise OpenError("the message isn't a JSON object")
    return payload


def pairing_code(key_a: str, key_b: str) -> str:
    """Six digits, e.g. "482 913", worked out from both public keys.

    Both laptops get the same code without sending it: the keys are sorted
    first, so the order doesn't matter. If someone in the middle swapped in
    their own key, the two screens would show different codes.
    """
    digest = hashlib.sha256("".join(sorted([key_a, key_b])).encode()).digest()
    number = int.from_bytes(digest[:8], "big") % 1_000_000
    return f"{number // 1000:03d} {number % 1000:03d}"
