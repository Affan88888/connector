import argparse
import asyncio
import getpass
import sys

import qasync
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from connector.net.discovery import Discovery
from connector.ui.dock import SideDock


def main() -> None:
    args = parse_args()

    # One QApplication per program, before any window.
    app = QApplication(sys.argv)
    # The app lives at the edge of the screen. Hiding its windows (like the
    # panel while it's tucked away) must never make Qt end the app.
    app.setQuitOnLastWindowClosed(False)
    _let_python_notice_ctrl_c(app)

    try:
        # Like asyncio.run(), but the asyncio event loop it uses is Qt's event
        # loop underneath. One loop on one thread runs both the UI and the
        # async (network) code, taking turns.
        qasync.run(run(args.name))
    except KeyboardInterrupt:
        # How asyncio reports Ctrl+C once run() has finished cleaning up.
        pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="connector", description="Send links to laptops nearby.")
    parser.add_argument(
        "--name",
        default=default_name(),
        help="the name other people see (default: your Windows user name)",
    )
    # parse_known_args() leaves alone any options meant for Qt itself.
    args, _ = parser.parse_known_args()
    return args


def default_name() -> str:
    user = getpass.getuser()
    # "affan" -> "Affan". Only the first letter, so "JohnDoe" stays "JohnDoe".
    return user[:1].upper() + user[1:]


async def run(name: str) -> None:
    """Everything the app does, from start to shutdown."""
    dock = SideDock()
    dock.show()

    # Nothing listens for links yet, so we advertise port 0. Step 3 adds the
    # server and passes its real port here.
    discovery = Discovery(name, port=0)
    discovery.peer_added.connect(dock.panel.add_peer)
    discovery.peer_removed.connect(dock.panel.remove_peer)
    # Also report it in the terminal, which helps while testing.
    discovery.peer_added.connect(lambda peer: print(f"+ {peer.name} ({peer.address})"))
    discovery.peer_removed.connect(lambda peer: print(f"- {peer.name} left"))

    # Run until told to stop. For now that's only Ctrl+C: asyncio then cancels
    # this coroutine, and the `finally` below still runs. A Quit menu item
    # later will set this event instead.
    quit_requested = asyncio.Event()
    try:
        await discovery.start()
        print(f"Connector is running as {name!r}. Press Ctrl+C to quit.")
        await quit_requested.wait()
    finally:
        print("Shutting down...")
        # Sends the mDNS goodbye, so other laptops drop us straight away
        # instead of listing us for another hour.
        await discovery.stop()
        print("Bye.")


def _let_python_notice_ctrl_c(app: QApplication) -> None:
    # Python handles Ctrl+C between Python instructions only. While the app is
    # idle, the thread waits inside Qt's C++ code and Python never gets a
    # turn, so Ctrl+C would sit unnoticed. This timer runs a tiny bit of
    # Python five times a second, which gives it that turn.
    timer = QTimer(app)  # parented to app, so it lives as long as the app
    timer.timeout.connect(lambda: None)
    timer.start(200)
