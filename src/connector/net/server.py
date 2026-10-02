"""A small HTTP server that receives links from other laptops.

It listens on a free port chosen by the OS. Discovery advertises that port
over mDNS, which is how other laptops know where to send.

Step 3 has no pairing or encryption: anyone on the same network can send
here, and the sender's name is whatever they claim. Step 4 fixes that.
"""

from aiohttp import web
from PySide6.QtCore import QObject, Signal

from connector.core.links import Link, is_shareable

SHARE_PATH = "/v1/share"
MAX_BODY_BYTES = 16 * 1024  # a link and a title fit easily; bigger is refused
MAX_NAME_LENGTH = 64
MAX_TITLE_LENGTH = 200


class LinkServer(QObject):
    link_received = Signal(str, object)  # (sender's name, Link)

    def __init__(self) -> None:
        super().__init__()
        self._runner: web.AppRunner | None = None

    async def start(self) -> int:
        """Start listening. Returns the port the OS picked."""
        app = web.Application(client_max_size=MAX_BODY_BYTES)
        app.add_routes([web.post(SHARE_PATH, self._share)])
        # shutdown_timeout: on quit, wait at most 1 s for a link still being
        # received. aiohttp's default of 60 s could hold up Ctrl+C.
        self._runner = web.AppRunner(app, access_log=None, shutdown_timeout=1.0)
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

    async def _share(self, request: web.Request) -> web.Response:
        """POST /v1/share with {"from": "Bob", "url": "https://…", "title": "…"}."""
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "the body isn't JSON")
        if not isinstance(body, dict):
            return _error(400, "the body isn't a JSON object")

        # Check everything coming from the network before using it: it's
        # whatever the other side chose to send.
        url = body.get("url")
        if not isinstance(url, str) or not is_shareable(url):
            return _error(400, "not an http(s) link")
        sender = body.get("from")
        if not isinstance(sender, str) or not sender.strip():
            sender = "Someone"
        title = body.get("title")
        if not isinstance(title, str) or not title.strip():
            title = None

        link = Link(url, title.strip()[:MAX_TITLE_LENGTH] if title else None)
        self.link_received.emit(sender.strip()[:MAX_NAME_LENGTH], link)
        # 202 Accepted: we have it, and what happens next is up to this laptop.
        return web.json_response({"status": "delivered"}, status=202)


def _error(status: int, message: str) -> web.Response:
    return web.json_response({"error": message}, status=status)
