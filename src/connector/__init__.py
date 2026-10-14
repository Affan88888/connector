import argparse
import asyncio
import getpass
import sys

import qasync
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from connector.app import App


def main() -> None:
    args = parse_args()
    # Page titles can contain any character. When the output goes somewhere
    # other than a real console (a file, a pipe), printing one Windows can't
    # encode would crash the app; this prints a ? in its place instead.
    if sys.stdout is not None:
        sys.stdout.reconfigure(errors="replace")

    # One QApplication per program, before any window.
    qt_app = QApplication(sys.argv)
    # The app lives at the edge of the screen. Hiding its windows (like the
    # panel while it's tucked away) must never make Qt end the app.
    qt_app.setQuitOnLastWindowClosed(False)
    _let_python_notice_ctrl_c(qt_app)

    try:
        # Like asyncio.run(), but the asyncio event loop it uses is Qt's event
        # loop underneath. One loop on one thread runs both the UI and the
        # async (network) code, taking turns.
        qasync.run(run(args.name, args.profile))
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
    parser.add_argument(
        "--profile",
        default="default",
        help="keeps its own key and pairings, e.g. to test two copies on one laptop",
    )
    # parse_known_args() leaves alone any options meant for Qt itself.
    args, _ = parser.parse_known_args()
    return args


def default_name() -> str:
    user = getpass.getuser()
    # "affan" -> "Affan". Only the first letter, so "JohnDoe" stays "JohnDoe".
    return user[:1].upper() + user[1:]


async def run(name: str, profile: str) -> None:
    """The app's whole life: start everything, wait, stop everything."""
    app = App(name, profile)
    # Run until told to stop. For now that's only Ctrl+C: asyncio then cancels
    # this coroutine, and the `finally` below still runs. A Quit menu item
    # later will set this event instead.
    quit_requested = asyncio.Event()
    try:
        await app.start()
        await quit_requested.wait()
    finally:
        print("Shutting down...")
        await app.stop()
        print("Bye.")


def _let_python_notice_ctrl_c(qt_app: QApplication) -> None:
    # Python handles Ctrl+C between Python instructions only. While the app is
    # idle, the thread waits inside Qt's C++ code and Python never gets a
    # turn, so Ctrl+C would sit unnoticed. This timer runs a tiny bit of
    # Python five times a second, which gives it that turn.
    timer = QTimer(qt_app)  # parented to the app, so it lives as long as the app
    timer.timeout.connect(lambda: None)
    timer.start(200)
