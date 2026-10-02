"""Reading a link out of what a browser hands over when you drag something.

A drag carries a QMimeData: the same content in several formats at once,
each labelled with a type such as "text/uri-list" or "text/html". Browsers
fill in different ones, so this checks them in order of preference.
"""

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QTextDocumentFragment

from connector.core.links import Link, is_shareable

MAX_TITLE_LENGTH = 200


def link_from_drag(mime: QMimeData) -> Link | None:
    """The web link being dragged, or None if it isn't one."""
    url = _url(mime)
    if url is None:
        return None
    return Link(url, _title(mime, url))


def _url(mime: QMimeData) -> str | None:
    # Best: the drag says outright that it carries links ("text/uri-list").
    # Files dragged from Explorer arrive this way too, as file:// links,
    # which is_shareable() turns away.
    for qurl in mime.urls():
        url = qurl.toString()
        if is_shareable(url):
            return url
    # Some drags carry the address only as plain text.
    if mime.hasText():
        text = mime.text().strip()
        if not any(c.isspace() for c in text) and is_shareable(text):
            return text
    return None


def _title(mime: QMimeData, url: str) -> str | None:
    # Firefox: "text/x-moz-url" holds the URL and the title on two lines.
    # On Windows, Qt lists it under a longer name, hence the substring check.
    for fmt in mime.formats():
        if "x-moz-url" in fmt:
            text = bytes(mime.data(fmt)).decode("utf-16-le", errors="ignore").replace("\x00", "")
            lines = text.splitlines()
            if len(lines) > 1 and lines[1].strip():
                return lines[1].strip()[:MAX_TITLE_LENGTH]
    # Chrome and Edge: a bit of HTML like <a href="...">Page title</a>.
    # QTextDocumentFragment turns HTML into its plain text.
    if mime.hasHtml():
        text = QTextDocumentFragment.fromHtml(mime.html()).toPlainText().strip()
        if text and text != url and not is_shareable(text):
            return text[:MAX_TITLE_LENGTH]
    return None
