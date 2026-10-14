"""What we know about another laptop running Connector."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Peer:
    id: str  # unique while that app runs: its mDNS instance name
    name: str  # display name, e.g. "Bob"
    address: str  # IP address on the local network
    port: int  # where its app listens for links and pairing requests
    public_key: str  # who it is, across restarts (base64); see core/identity.py
