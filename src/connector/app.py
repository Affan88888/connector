"""Connector's parts, and how they're wired together."""

import asyncio
from collections.abc import Coroutine
from pathlib import Path

from platformdirs import user_data_path

from connector.core.crypto import pairing_code
from connector.core.identity import Identity
from connector.core.links import Link
from connector.core.peers import Peer
from connector.core.store import Store
from connector.net.client import LinkClient
from connector.net.discovery import Discovery
from connector.net.server import LinkServer, PairingRequest
from connector.opener import open_link
from connector.ui.dock import SideDock


def data_dir(profile: str) -> Path:
    """Where a profile's database lives, e.g. on Windows
    C:\\Users\\you\\AppData\\Local\\Connector\\profiles\\default"""
    return user_data_path("Connector", appauthor=False) / "profiles" / profile


class App:
    """Starts every part, connects them to each other, and stops them."""

    def __init__(self, name: str, profile: str) -> None:
        self.name = name
        self.identity = Identity.load_or_create(profile)
        self.store = Store(data_dir(profile) / "connector.db")
        self.dock = SideDock()
        self.server = LinkServer(self.identity, self.store)
        self.client = LinkClient(name, self.identity, self.store)
        # Discovery advertises the server's port, which is only known once
        # the server has started, so it's created in start().
        self.discovery: Discovery | None = None
        self._tasks: set[asyncio.Task[None]] = set()  # keeps background tasks alive
        self._pairing: asyncio.Task[None] | None = None  # our request to pair, while it waits
        self._incoming: PairingRequest | None = None  # someone's request, while we decide

        panel = self.dock.panel
        panel.send_requested.connect(self._on_send_requested)
        panel.pair_requested.connect(self._start_pairing)
        self.server.link_received.connect(self._on_link_received)
        self.server.pair_requested.connect(self._on_pair_requested)
        self.server.pair_withdrawn.connect(self._on_pair_withdrawn)
        self.server.peer_paired.connect(self._on_peer_paired)

    async def start(self) -> None:
        self.dock.show()
        await self.client.start()
        port = await self.server.start()

        self.discovery = Discovery(self.name, port, self.identity.public_key_text)
        self.discovery.peer_added.connect(
            lambda peer: self.dock.panel.add_peer(peer, paired=self.store.is_paired(peer.public_key))
        )
        self.discovery.peer_removed.connect(self.dock.panel.remove_peer)
        # Also report it in the terminal, which helps while testing.
        self.discovery.peer_added.connect(lambda peer: print(f"+ {peer.name} ({peer.address}:{peer.port})"))
        self.discovery.peer_removed.connect(lambda peer: print(f"- {peer.name} left"))
        await self.discovery.start()
        print(f"Connector is running as {self.name!r}, receiving links on port {port}. Press Ctrl+C to quit.")

    async def stop(self) -> None:
        """Stop everything. Safe to call even if start() didn't finish."""
        for task in list(self._tasks):
            task.cancel()
        if self._incoming is not None:
            self._incoming.decide(False)
        # The goodbye first, so other laptops drop us before we stop listening.
        if self.discovery is not None:
            await self.discovery.stop()
        await self.server.stop()
        await self.client.stop()
        self.store.close()

    def _spawn(self, coroutine: Coroutine) -> asyncio.Task:
        # Signal handlers are plain functions and can't await, so async work
        # they start runs as its own task, kept in _tasks while it runs.
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # --- sending ----------------------------------------------------------

    def _on_send_requested(self, peer: Peer, link: Link) -> None:
        if self.store.is_paired(peer.public_key):
            self._spawn(self._send(peer, link))
        else:
            # Not paired yet: pair first, then send it if they accept.
            self._start_pairing(peer, then_send=link)

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

    # --- pairing we ask for -----------------------------------------------

    def _start_pairing(self, peer: Peer, then_send: Link | None = None) -> None:
        if self._pairing is not None or self._incoming is not None:
            # One pairing at a time; the card is already in use.
            if then_send is not None:
                self.dock.panel.show_send_result(peer, arrived=False)
            return
        code = pairing_code(self.identity.public_key_text, peer.public_key)
        self.dock.card.show_question(
            f"Pairing with {peer.name}",
            code,
            f"Check that {peer.name}'s screen shows the same code.",
            [("Cancel", False, self._cancel_pairing)],
            self.dock.beside(),
        )
        print(f"Asking {peer.name} to pair (code {code})")
        self._pairing = self._spawn(self._pair(peer, then_send))

    def _cancel_pairing(self) -> None:
        if self._pairing is not None:
            # Cancelling the task closes the request's connection, which is
            # how the other laptop notices and closes its card.
            self._pairing.cancel()

    async def _pair(self, peer: Peer, then_send: Link | None) -> None:
        try:
            problem = await self.client.pair(peer)
        except asyncio.CancelledError:
            self.dock.card.close_card()
            if then_send is not None:
                self.dock.panel.show_send_result(peer, arrived=False)
            print(f"Pairing with {peer.name} cancelled")
            raise
        finally:
            self._pairing = None

        if problem is not None:
            self.dock.card.show_message(problem, self.dock.beside())
            if then_send is not None:
                self.dock.panel.show_send_result(peer, arrived=False)
            print(f"Couldn't pair with {peer.name}: {problem}")
            return
        self.dock.panel.set_paired(peer.public_key)
        self.dock.card.show_message(f"Paired with {peer.name}", self.dock.beside())
        print(f"Paired with {peer.name}")
        if then_send is not None:
            await self._send(peer, then_send)

    # --- pairing someone else asks for ------------------------------------

    def _on_pair_requested(self, request: PairingRequest) -> None:
        if self._pairing is not None:
            request.decide(False)  # we're busy asking someone ourselves
            return
        self._incoming = request

        def answer(accepted: bool) -> None:
            self._incoming = None
            request.decide(accepted)  # the server's waiting request carries on
            if not accepted:
                self.dock.card.close_card()

        self.dock.card.show_question(
            f"{request.name} wants to pair",
            request.code,
            f"Accept only if {request.name}'s screen shows this same code.",
            [("Decline", False, lambda: answer(False)), ("Accept", True, lambda: answer(True))],
            self.dock.beside(),
        )
        print(f"{request.name} wants to pair (code {request.code})")

    def _on_pair_withdrawn(self, request: PairingRequest) -> None:
        if self._incoming is request:
            self._incoming = None
            self.dock.card.show_message(f"{request.name} cancelled", self.dock.beside())

    def _on_peer_paired(self, public_key: str) -> None:
        name = self.store.paired_name(public_key) or "Someone"
        self.dock.panel.set_paired(public_key)
        self.dock.card.show_message(f"Paired with {name}", self.dock.beside())
        print(f"Paired with {name}")
