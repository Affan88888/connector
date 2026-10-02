"""Sends links to other laptops' LinkServers."""

import asyncio

import aiohttp

from connector.core.links import Link, is_shareable
from connector.core.peers import Peer
from connector.net.server import SHARE_PATH

TIMEOUT_S = 5  # a laptop in the same room answers in milliseconds


class LinkClient:
    def __init__(self, my_name: str) -> None:
        self._my_name = my_name
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        # One session for the app's lifetime: it reuses connections, and
        # creating one per send is slower and warned against by aiohttp.
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=TIMEOUT_S))

    async def stop(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def send(self, peer: Peer, link: Link) -> str | None:
        """Send a link to a peer. Returns None if it arrived, otherwise a
        short reason why not."""
        if self._session is None:
            return "not started"
        if not is_shareable(link.url):
            return "not an http(s) link"
        if peer.port == 0:
            return "their Connector can't receive links yet (an older version?)"

        url = f"http://{peer.address}:{peer.port}{SHARE_PATH}"
        body = {"from": self._my_name, "url": link.url, "title": link.title}
        try:
            async with self._session.post(url, json=body) as response:
                if response.status == 202:
                    return None
                return f"they refused it (HTTP {response.status})"
        except asyncio.TimeoutError:
            return "no answer (a firewall may be blocking it)"
        except aiohttp.ClientConnectorError:
            # Refused or unreachable: usually their app has just closed.
            return "couldn't reach them (their app may have closed)"
        except aiohttp.ClientError as error:
            return f"the connection failed ({type(error).__name__})"
