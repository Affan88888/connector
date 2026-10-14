"""A small HTTP server that receives pairing requests and links.

It listens on a free port chosen by the OS. Discovery advertises that port
over mDNS, which is how other laptops know where to send.

Two routes:
- POST /v1/pair   Someone asks to pair. The request waits, up to a minute,
                  while this laptop's user compares the code and decides.
- POST /v1/share  A link, sealed (see core/crypto.py). Only accepted from
                  paired peers, only if it opens, and only once.
"""

import asyncio
import time

from aiohttp import web
from PySide6.QtCore import QObject, Signal

from connector.core.crypto import OpenError, open_sealed, pairing_code
from connector.core.identity import Identity, is_valid_public_key
from connector.core.links import Link, is_shareable
from connector.core.store import Store

PAIR_PATH = "/v1/pair"
SHARE_PATH = "/v1/share"
PAIR_TIMEOUT_S = 60  # how long someone has to accept or decline
MAX_BODY_BYTES = 16 * 1024  # a link and a title fit easily; bigger is refused
MAX_NAME_LENGTH = 64
MAX_TITLE_LENGTH = 200
MAX_MSG_ID_LENGTH = 64
# How far apart two laptops' clocks may be. A sealed message older than
# this is refused, so a recording of one can't be replayed much later.
MAX_CLOCK_DIFFERENCE_S = 120


class PairingRequest:
    """Someone asking to pair, waiting for this laptop's user to decide."""

    def __init__(self, name: str, public_key: str, code: str) -> None:
        self.name = name
        self.public_key = public_key
        self.code = code
        # A Future is a value that will arrive later: here, the decision.
        # The waiting request handler awaits it; decide() fills it in.
        self.decision: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

    def decide(self, accepted: bool) -> None:
        if not self.decision.done():
            self.decision.set_result(accepted)


class LinkServer(QObject):
    link_received = Signal(str, object)  # (sender's name, Link)
    pair_requested = Signal(object)  # a PairingRequest to show the user
    pair_withdrawn = Signal(object)  # that PairingRequest is over without an answer
    peer_paired = Signal(str)  # the public key of someone we just paired with

    def __init__(self, me: Identity, store: Store) -> None:
        super().__init__()
        self._me = me
        self._store = store
        self._runner: web.AppRunner | None = None
        self._pending: PairingRequest | None = None  # one pairing prompt at a time

    async def start(self) -> int:
        """Start listening. Returns the port the OS picked."""
        app = web.Application(client_max_size=MAX_BODY_BYTES)
        app.add_routes([web.post(PAIR_PATH, self._pair), web.post(SHARE_PATH, self._share)])
        self._runner = web.AppRunner(
            app,
            access_log=None,
            # On quit, wait at most 1 s for a request still in progress.
            # aiohttp's default of 60 s could hold up Ctrl+C.
            shutdown_timeout=1.0,
            # Stop a request's handler if the other side disconnects. That's
            # how we notice someone cancelled pairing and close our prompt.
            handler_cancellation=True,
        )
        await self._runner.setup()
        # 0.0.0.0: every network interface, so other laptops can reach us.
        # Port 0: "any free port", so two copies on one laptop don't clash.
        site = web.TCPSite(self._runner, host="0.0.0.0", port=0)
        await site.start()
        return self._runner.addresses[0][1]

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    # --- POST /v1/pair ----------------------------------------------------

    async def _pair(self, request: web.Request) -> web.Response:
        """{"pk": "<their public key>", "name": "Alice"}"""
        body = await _json_object(request)
        if body is None:
            return _error(400, "the body isn't a JSON object")
        key = body.get("pk")
        if not is_valid_public_key(key) or key == self._me.public_key_text:
            return _error(400, "not a valid public key")
        name = _clean_name(body.get("name"))

        if self._store.is_paired(key):
            # Already trusted (they may have lost their copy): no need to ask.
            self._store.add_paired(key, name)
            return web.json_response({"status": "accepted"})
        if self._pending is not None:
            return _error(409, "busy with another pairing request")

        pending = PairingRequest(name, key, pairing_code(self._me.public_key_text, key))
        self._pending = pending
        self.pair_requested.emit(pending)
        try:
            accepted = await asyncio.wait_for(pending.decision, PAIR_TIMEOUT_S)
        except asyncio.TimeoutError:
            self.pair_withdrawn.emit(pending)
            return _error(408, "no answer in time")
        except asyncio.CancelledError:
            # They cancelled (closed the connection), or we're quitting.
            self.pair_withdrawn.emit(pending)
            raise
        finally:
            self._pending = None

        if not accepted:
            return _error(403, "declined")
        self._store.add_paired(key, name)
        self.peer_paired.emit(key)
        return web.json_response({"status": "accepted"})

    # --- POST /v1/share ---------------------------------------------------

    async def _share(self, request: web.Request) -> web.Response:
        """{"from": "<their public key>", "box": "<sealed payload>"}"""
        body = await _json_object(request)
        if body is None:
            return _error(400, "the body isn't a JSON object")
        key, sealed = body.get("from"), body.get("box")
        if not isinstance(key, str) or not self._store.is_paired(key):
            return _error(403, "not paired")
        if not isinstance(sealed, str):
            return _error(400, "no sealed message")

        # Opening proves it came from whoever holds that key's private half,
        # and that nothing was changed on the way.
        try:
            payload = open_sealed(self._me, key, sealed)
        except OpenError:
            return _error(400, "the message didn't open")

        url, title = payload.get("url"), payload.get("title")
        msg_id, sent_at = payload.get("msg_id"), payload.get("sent_at")
        if payload.get("type") != "link" or not isinstance(url, str) or not is_shareable(url):
            return _error(400, "not an http(s) link")
        if not isinstance(msg_id, str) or not 0 < len(msg_id) <= MAX_MSG_ID_LENGTH:
            return _error(400, "no message id")
        if not isinstance(sent_at, (int, float)) or abs(time.time() - sent_at) > MAX_CLOCK_DIFFERENCE_S:
            return _error(400, "the message is too old (or a clock is wrong)")
        if not self._store.first_arrival(msg_id, key, url):
            return _error(409, "already received")

        if not isinstance(title, str) or not title.strip():
            title = None
        link = Link(url, title.strip()[:MAX_TITLE_LENGTH] if title else None)
        self.link_received.emit(self._store.paired_name(key) or "Someone", link)
        # 202 Accepted: we have it, and what happens next is up to this laptop.
        return web.json_response({"status": "delivered"}, status=202)


async def _json_object(request: web.Request) -> dict | None:
    try:
        body = await request.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _clean_name(name: object) -> str:
    if not isinstance(name, str) or not name.strip():
        return "Someone"
    return name.strip()[:MAX_NAME_LENGTH]


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)
