"""Asks other laptops to pair, and sends them sealed links."""

import asyncio
import time
import uuid

import aiohttp

from connector.core.crypto import seal
from connector.core.identity import Identity
from connector.core.links import Link, is_shareable
from connector.core.peers import Peer
from connector.core.store import Store
from connector.net.server import PAIR_PATH, PAIR_TIMEOUT_S, SHARE_PATH

TIMEOUT_S = 5  # a laptop in the same room answers in milliseconds


class LinkClient:
    def __init__(self, my_name: str, me: Identity, store: Store) -> None:
        self._my_name = my_name
        self._me = me
        self._store = store
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        # One session for the app's lifetime: it reuses connections, and
        # creating one per request is slower and warned against by aiohttp.
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_S))

    async def stop(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def pair(self, peer: Peer) -> str | None:
        """Ask a peer to pair. Waits while they decide (up to a minute).
        Returns None if they accepted, otherwise a short reason why not."""
        body = {"pk": self._me.public_key_text, "name": self._my_name}
        # They have PAIR_TIMEOUT_S to decide; give the request a little longer.
        status, problem = await self._post(peer, PAIR_PATH, body, PAIR_TIMEOUT_S + 5)
        if problem is not None:
            return problem
        if status == 200:
            self._store.add_paired(peer.public_key, peer.name)
            return None
        return {
            403: f"{peer.name} declined",
            408: f"{peer.name} didn't answer",
            409: f"{peer.name} is busy pairing with someone else",
        }.get(status, f"{peer.name} refused (HTTP {status})")

    async def send(self, peer: Peer, link: Link) -> str | None:
        """Send a sealed link to a paired peer. Returns None if it arrived,
        otherwise a short reason why not."""
        if not is_shareable(link.url):
            return "not an http(s) link"
        if not self._store.is_paired(peer.public_key):
            return "not paired with them"

        payload = {
            "type": "link",
            "url": link.url,
            "title": link.title,
            # A fresh id per message, and the time: together they let the
            # receiver refuse a copy of this message sent again later.
            "msg_id": uuid.uuid4().hex,
            "sent_at": time.time(),
        }
        body = {"from": self._me.public_key_text, "box": seal(self._me, peer.public_key, payload)}
        status, problem = await self._post(peer, SHARE_PATH, body, TIMEOUT_S)
        if problem is not None:
            return problem
        if status == 202:
            return None
        if status == 403:
            return "they don't have you as paired (they may have reset Connector)"
        return f"they refused it (HTTP {status})"

    async def _post(self, peer: Peer, path: str, body: dict, timeout_s: float) -> tuple[int, str | None]:
        """POST JSON to a peer. Returns (status, None), or (0, reason) if it
        never got an answer."""
        if self._session is None:
            return 0, "not started"
        url = f"http://{peer.address}:{peer.port}{path}"
        try:
            async with self._session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=timeout_s)) as response:
                return response.status, None
        except asyncio.TimeoutError:
            return 0, "no answer (a firewall may be blocking it)"
        except aiohttp.ClientConnectorError:
            # Refused or unreachable: usually their app has just closed.
            return 0, "couldn't reach them (their app may have closed)"
        except aiohttp.ClientError as error:
            return 0, f"the connection failed ({type(error).__name__})"
