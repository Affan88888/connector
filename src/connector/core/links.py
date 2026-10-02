"""Links that can be shared, and the rule for which ones are allowed."""

from dataclasses import dataclass
from urllib.parse import urlsplit

MAX_URL_LENGTH = 2048
ALLOWED_SCHEMES = ("http", "https")


@dataclass(frozen=True)
class Link:
    url: str
    title: str | None = None  # the page's title, if the browser passed it along


def is_shareable(url: str) -> bool:
    """Only ordinary web links: http:// or https:// with a host.

    Everything else is refused: file: (someone's files), javascript:, and
    custom schemes like ms-msdt:, which have been used to launch programs.
    The sender checks this before sending, and in step 3 the receiving
    laptop checks it again before opening anything.
    """
    if len(url) > MAX_URL_LENGTH:
        return False
    parts = urlsplit(url)
    return parts.scheme.lower() in ALLOWED_SCHEMES and bool(parts.netloc)
