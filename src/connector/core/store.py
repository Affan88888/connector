"""What this laptop remembers between runs, in a small SQLite database:
who you've paired with, and which links have already arrived.

All of it runs on the app's one thread. SQLite calls block, but these are
tiny reads and writes that take well under a millisecond.
"""

import sqlite3
import time
from pathlib import Path

# One script per database version. On start, any not yet run are run in
# order, and PRAGMA user_version records how far it got. To change the
# database later, add a script; never edit one that has shipped.
MIGRATIONS = [
    """
    CREATE TABLE peers (
        public_key TEXT PRIMARY KEY,  -- base64; this is who they are
        name       TEXT NOT NULL,     -- what they were called when we paired
        paired_at  REAL NOT NULL      -- seconds since 1970
    );
    CREATE TABLE received (
        msg_id      TEXT PRIMARY KEY, -- each message's own id: how a replay is caught
        sender_key  TEXT NOT NULL,
        url         TEXT NOT NULL,
        received_at REAL NOT NULL
    );
    """,
]


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._migrate()

    def _migrate(self) -> None:
        version = self._db.execute("PRAGMA user_version").fetchone()[0]
        for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
            self._db.executescript(script)
            self._db.execute(f"PRAGMA user_version = {number}")
            self._db.commit()

    def close(self) -> None:
        self._db.close()

    # --- paired peers -----------------------------------------------------

    def is_paired(self, public_key: str) -> bool:
        return self.paired_name(public_key) is not None

    def paired_name(self, public_key: str) -> str | None:
        row = self._db.execute("SELECT name FROM peers WHERE public_key = ?", (public_key,)).fetchone()
        return row[0] if row else None

    def add_paired(self, public_key: str, name: str) -> None:
        # The ? placeholders pass values separately from the SQL, so a name
        # like "Bob'); DROP TABLE peers;--" is just an odd name, never SQL.
        with self._db:  # commits at the end of the block
            self._db.execute(
                """INSERT INTO peers (public_key, name, paired_at) VALUES (?, ?, ?)
                   ON CONFLICT (public_key) DO UPDATE SET name = excluded.name""",
                (public_key, name, time.time()),
            )

    # --- received links ---------------------------------------------------

    def first_arrival(self, msg_id: str, sender_key: str, url: str) -> bool:
        """Record a message. False if this msg_id has arrived before: a replay."""
        try:
            with self._db:
                self._db.execute(
                    "INSERT INTO received (msg_id, sender_key, url, received_at) VALUES (?, ?, ?, ?)",
                    (msg_id, sender_key, url, time.time()),
                )
        except sqlite3.IntegrityError:  # msg_id is the PRIMARY KEY, so no duplicates
            return False
        return True
