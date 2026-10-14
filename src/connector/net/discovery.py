"""Finds other laptops running Connector on the local network, and lets them
find us, using mDNS. spikes/discovery_spike.py shows the same steps in their
simplest form.

Discovery doesn't know the UI exists. It announces what happens through Qt
signals, and whatever is interested connects to them.
"""

import asyncio
import re
import socket
import uuid

from PySide6.QtCore import QObject, Signal
from zeroconf import ServiceStateChange, Zeroconf
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

from connector.core.identity import is_valid_public_key
from connector.core.peers import Peer

SERVICE_TYPE = "_connector._tcp.local."
# 2: links are sealed and need pairing. Copies speaking another version are
# left out of the panel rather than shown and failing.
PROTOCOL_VERSION = "2"
LOOKUP_TIMEOUT_MS = 3000


def lan_ip() -> str:
    """This laptop's address on the local network."""
    try:
        # Connecting a UDP socket sends nothing. It only makes the OS pick
        # the network interface it would use to reach that address.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        # No network at all (e.g. Wi-Fi off). Only this laptop can find us
        # then, which still allows testing two copies on it.
        return "127.0.0.1"


def instance_label(name: str) -> str:
    """A unique mDNS instance label for this run, e.g. "Affan-3f9c2a".

    The random part stops two people called Affan clashing. Dots would split
    the label in two, so they're replaced, and labels must stay short. The
    display name itself travels untouched in the TXT record.
    """
    safe = re.sub(r"[.\s]+", "-", name).strip("-")[:40] or "connector"
    return f"{safe}-{uuid.uuid4().hex[:6]}"


def decode_properties(raw: dict[bytes, bytes | None]) -> dict[str, str]:
    # TXT records arrive as bytes on both sides, e.g. {b"name": b"Bob"}.
    return {k.decode(): (v.decode() if v is not None else "") for k, v in raw.items()}


class Discovery(QObject):
    """Announces this laptop on the local network and watches for others."""

    peer_added = Signal(object)  # a Peer; sent again if its details change
    peer_removed = Signal(object)  # the Peer that left

    def __init__(self, name: str, port: int, public_key: str) -> None:
        super().__init__()
        self._name = name
        self._port = port
        self._public_key = public_key
        self._instance = f"{instance_label(name)}.{SERVICE_TYPE}"
        self._zeroconf: AsyncZeroconf | None = None
        self._browser: AsyncServiceBrowser | None = None
        self._present: set[str] = set()  # instance names currently announced
        self._peers: dict[str, Peer] = {}  # the ones we've looked up, by id
        self._lookups: set[asyncio.Task[None]] = set()  # keeps lookups alive

    async def start(self) -> None:
        info = AsyncServiceInfo(
            SERVICE_TYPE,
            self._instance,
            port=self._port,
            # A public key is 44 characters of base64; TXT values can hold 255.
            properties={"v": PROTOCOL_VERSION, "name": self._name, "pk": self._public_key},
            parsed_addresses=[lan_ip()],
        )
        self._zeroconf = AsyncZeroconf()
        # Checks nobody else has our instance name (about a second), then
        # announces us.
        await self._zeroconf.async_register_service(info)
        self._browser = AsyncServiceBrowser(
            self._zeroconf.zeroconf, [SERVICE_TYPE], handlers=[self._on_change]
        )

    async def stop(self) -> None:
        """Say goodbye, so other laptops drop us straight away. Safe to call
        even if start() didn't get to finish."""
        for task in self._lookups:
            task.cancel()
        if self._browser is not None:
            await self._browser.async_cancel()
            self._browser = None
        if self._zeroconf is not None:
            await self._zeroconf.async_unregister_all_services()  # the goodbye packets
            await self._zeroconf.async_close()
            self._zeroconf = None

    def _on_change(
        self, zeroconf: Zeroconf, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        if name == self._instance:
            return  # that's us

        if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
            self._present.add(name)
            # zeroconf calls this in the middle of handling a packet, so it
            # can't wait for the lookup here. The lookup runs as its own task.
            task = asyncio.create_task(self._look_up(zeroconf, service_type, name))
            self._lookups.add(task)
            task.add_done_callback(self._lookups.discard)
        elif state_change is ServiceStateChange.Removed:
            self._present.discard(name)
            peer = self._peers.pop(name, None)
            if peer is not None:
                self.peer_removed.emit(peer)

    async def _look_up(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        if not await info.async_request(zeroconf, LOOKUP_TIMEOUT_MS):
            return  # it didn't answer; it may have just left
        if name not in self._present:
            return  # it left while we were asking

        addresses = info.parsed_addresses()
        if not addresses:
            return
        props = decode_properties(info.properties)
        if props.get("v") != PROTOCOL_VERSION or not is_valid_public_key(props.get("pk")):
            return  # a different version of Connector: we couldn't talk to it
        peer = Peer(
            id=name,
            name=props.get("name") or name.split(".")[0],
            address=addresses[0],
            port=info.port or 0,
            public_key=props["pk"],
        )
        if self._peers.get(name) == peer:
            return  # nothing new
        self._peers[name] = peer
        self.peer_added.emit(peer)
