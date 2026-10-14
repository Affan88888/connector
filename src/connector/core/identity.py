"""This laptop's identity: a key pair that stays the same across restarts.

The public key is shared freely; it's in our mDNS record. The private key
never leaves this laptop. It's kept in the operating system's credential
store (Windows Credential Manager), not in a file next to the app, so
copying the app's files doesn't copy who it is.
"""

import base64

import keyring
from nacl.public import PrivateKey, PublicKey

KEYRING_SERVICE = "Connector"


def encode_key(key: PublicKey | PrivateKey) -> str:
    """A key as text (base64), 44 characters, for JSON and mDNS."""
    return base64.b64encode(bytes(key)).decode()


def decode_public_key(text: str) -> PublicKey:
    """Raises ValueError (or TypeError) if `text` isn't a valid public key."""
    return PublicKey(base64.b64decode(text, validate=True))


def is_valid_public_key(text: object) -> bool:
    if not isinstance(text, str):
        return False
    try:
        decode_public_key(text)
    except (ValueError, TypeError):
        return False
    return True


class Identity:
    def __init__(self, private_key: PrivateKey) -> None:
        self.private_key = private_key
        self.public_key_text = encode_key(private_key.public_key)

    @classmethod
    def load_or_create(cls, profile: str) -> "Identity":
        """This profile's key from the credential store, made on first run."""
        account = f"device-key/{profile}"
        stored = keyring.get_password(KEYRING_SERVICE, account)
        if stored is None:
            private_key = PrivateKey.generate()
            keyring.set_password(KEYRING_SERVICE, account, encode_key(private_key))
            return cls(private_key)
        return cls(PrivateKey(base64.b64decode(stored)))
