"""Connector's parts, and how they're wired together."""

import asyncio

from connector.core.links import Link
from connector.core.peers import Peer
from connector.net.client import LinkClient
from connector.net.discovery import Discovery
from connector.net.server import LinkServer
from connector.opener import open_link
from connector.ui.dock import SideDock


class App:
    """Starts every part, connects them to each other, and stops them."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.dock = SideDock()
        self.server = LinkServer()
        self.client = LinkClient(name)
        # Discovery advertises the server's port, which is only known once
        # the server has started, so it's created in start().
        self.discovery: Discovery | None = None
        self._sends: set[asyncio.Task[None]] = set()  # keeps sends alive

        self.dock.panel.send_requested.connect(self._on_send_requested)
        self.server.link_received.connect(self._on_link_received)

    async def start(self) -> None:
        self.dock.show()
        await self.client.start()
        port = await self.server.start()

        self.discovery = Discovery(self.name, port)
        self.discovery.peer_added.connect(self.dock.panel.add_peer)
        self.discovery.peer_removed.connect(self.dock.panel.remove_peer)
        # Also report it in the terminal, which helps while testing.
        self.discovery.peer_added.connect(lambda peer: print(f"+ {peer.name} ({peer.address}:{peer.port})"))
        self.discovery.peer_removed.connect(lambda peer: print(f"- {peer.name} left"))
        await self.discovery.start()
        print(f"Connector is running as {self.name!r}, receiving links on port {port}. Press Ctrl+C to quit.")

    async def stop(self) -> None:
        """Stop everything. Safe to call even if start() didn't finish."""
        for task in self._sends:
            task.cancel()
        # The goodbye first, so other laptops drop us before we stop listening.
        if self.discovery is not None:
            await self.discovery.stop()
        await self.server.stop()
        await self.client.stop()

    # --- sending ----------------------------------------------------------

    def _on_send_requested(self, peer: Peer, link: Link) -> None:
        # Signal handlers are plain functions and can't await, so the
        # sending runs as its own task, the same as discovery's lookups.
        task = asyncio.create_task(self._send(peer, link))
        self._sends.add(task)
        task.add_done_callback(self._sends.discard)

    async def _send(self, peer: Peer, link: Link) -> None:
        problem = await self.client.send(peer, link)
        self.dock.panel.show_send_result(peer, arrived=problem is None)
        if problem is None:
            print(f"Sent to {peer.name}: {link.url}")
        else:
            print(f"Couldn't send to {peer.name}: {problem}")

    # --- receiving --------------------------------------------------------

    def _on_link_received(self, sender: str, link: Link) -> None:
        opened = open_link(link)
        self.dock.show_toast(sender, link)
        print(f"{sender} sent you: {link.url}" + ("" if opened else "  (couldn't open it)"))
