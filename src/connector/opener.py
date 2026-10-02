"""Opening received links on this laptop."""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from connector.core.links import Link, is_shareable


def open_link(link: Link) -> bool:
    """Open a link in the default browser. Returns False if it wasn't opened.

    The server has already checked the link, but this is the last step
    before something actually happens on this laptop, so it checks again.
    """
    if not is_shareable(link.url):
        return False
    # Qt's way of "open this like double-clicking it would": for a web link,
    # that's the default browser.
    return QDesktopServices.openUrl(QUrl(link.url))
