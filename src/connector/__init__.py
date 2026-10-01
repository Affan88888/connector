import signal
import sys

from PySide6.QtWidgets import QApplication

from connector.ui.dock import SideDock


def main() -> None:
    # Qt's event loop runs in C++, so Python never gets to handle Ctrl+C.
    # SIG_DFL hands it back to the OS, which ends the process. Milestone B
    # replaces this.
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # One QApplication per program, before any window.
    app = QApplication(sys.argv)

    # The dock owns both windows: the peer panel, and the arrow tab left at
    # the screen edge while the panel is hidden.
    dock = SideDock()
    dock.show()

    # Run the event loop until the app quits.
    sys.exit(app.exec())
