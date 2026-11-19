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
            CREATE TABLE IF NOT EXISTS api_spend (
                day TEXT PRIMARY KEY, reserved INTEGER NOT NULL CHECK (reserved >= 0)
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

    def completed_duplicate(self, fingerprint, earliest, now):
        row = self.db.execute(
            """SELECT r.id FROM runs r JOIN submissions s ON s.run_id=r.id
               WHERE s.fingerprint=? AND r.started>=? AND r.expires>? AND r.workbook IS NOT NULL
               AND r.report IS NOT NULL ORDER BY r.started DESC LIMIT 1""",(fingerprint,earliest,now)).fetchone()
        return row[0] if row else None

    def reserve_spend(self, day, amount, limit):
        if amount <= 0 or amount > limit:
            return False
        with self.db:
            result = self.db.execute(
                """INSERT INTO api_spend (day,reserved) VALUES (?,?)
                   ON CONFLICT(day) DO UPDATE SET reserved=reserved+excluded.reserved
                   WHERE reserved+excluded.reserved <= ?""",(day,amount,limit))
            return result.rowcount == 1

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

    def next_slot(self, now, window, maximum):
        row = self.db.execute(
            "SELECT started FROM runs WHERE started > ? ORDER BY started DESC LIMIT 1 OFFSET ?",
            (now - window, maximum - 1)).fetchone()
        return row[0] + window if row else now

    def download(self, run_id, kind):
        # The HTTP handler supplies one of these two known column names.
        if kind not in ("workbook", "report"):
            raise ValueError("Unknown export type.")
        row = self.db.execute(f"SELECT {kind} FROM runs WHERE id = ?", (run_id,)).fetchone()
        return row[0] if row else None

    def close(self):
        self.db.close()
