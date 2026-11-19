"""Private, time-limited storage for research status and validated exports."""

import json
import sqlite3


class JobStore:
    """Calls are serialized by the research service lock."""

    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, started REAL NOT NULL, expires REAL,
                metadata TEXT NOT NULL, workbook BLOB, report BLOB
            );
            CREATE TABLE IF NOT EXISTS submissions (
                token TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE
            );
        """)

    def restore(self):
        return {job_id: json.loads(metadata) for job_id, metadata in
                self.db.execute("SELECT id, metadata FROM runs")}

    def replay(self, token):
        return self.db.execute(
            "SELECT fingerprint, run_id FROM submissions WHERE token = ?", (token,)).fetchone()

    def active_duplicate(self, fingerprint):
        row = self.db.execute(
            """SELECT s.run_id FROM submissions s JOIN runs r ON r.id = s.run_id
               WHERE s.fingerprint = ? AND r.expires IS NULL LIMIT 1""", (fingerprint,)).fetchone()
        return row[0] if row else None

    def admit(self, run, started, token, fingerprint):
        with self.db:
            self.db.execute("INSERT INTO runs(id, started, metadata) VALUES (?, ?, ?)",
                            (run["id"], started, json.dumps(run, allow_nan=False)))
            self.alias(token, fingerprint, run["id"])

    def alias(self, token, fingerprint, run_id):
        self.db.execute("INSERT INTO submissions VALUES (?, ?, ?)", (token, fingerprint, run_id))

    def finish(self, run, expires, contents=None):
        files = contents or {}
        with self.db:
            self.db.execute("UPDATE runs SET expires=?, metadata=?, workbook=?, report=? WHERE id=?",
                            (expires, json.dumps(run, allow_nan=False), files.get("workbook"),
                             files.get("report"), run["id"]))

    def purge(self, now):
        with self.db:
            expired = [row[0] for row in self.db.execute("SELECT id FROM runs WHERE expires <= ?", (now,))]
            if expired:
                self.db.execute("DELETE FROM runs WHERE expires <= ?", (now,))
            return expired

    def starts_since(self, cutoff):
        return self.db.execute("SELECT COUNT(*) FROM runs WHERE started > ?", (cutoff,)).fetchone()[0]

    def download(self, run_id, kind):
        # The HTTP handler supplies one of these two known column names.
        if kind not in ("workbook", "report"):
            raise ValueError("Unknown export type.")
        row = self.db.execute(f"SELECT {kind} FROM runs WHERE id = ?", (run_id,)).fetchone()
        return row[0] if row else None

    def close(self):
        self.db.close()
