"""SQLite persistence — the enabler that makes universe scans fast and repeatable.

Three concerns, one small DB:
  * `snapshots`    — cache deterministic StockSnapshots so a re-scan skips network fetches.
  * `constituents` — cache fetched index membership so scans work offline after first fetch.
  * `scan_results` — persist every scanned row so scans can be diffed over time.

Uses only the stdlib `sqlite3`. Serialization piggybacks on pydantic's JSON round-trip.
The connection is created per-op (short-lived) and guarded by a lock — the batch scanner hits
this from a thread pool.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from indi_analyst.models import StockSnapshot
from indi_analyst.screener.models import Constituent, ScanResult, ScreenRow


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class ScanCache:
    """A tiny SQLite-backed store for snapshots, constituents, and scan history."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(os.path.expanduser(str(path)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    symbol   TEXT PRIMARY KEY,
                    as_of    TEXT NOT NULL,
                    json     TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS constituents (
                    universe   TEXT NOT NULL,
                    symbol     TEXT NOT NULL,
                    name       TEXT,
                    sector     TEXT,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (universe, symbol)
                );
                CREATE TABLE IF NOT EXISTS scan_results (
                    scan_id    TEXT NOT NULL,
                    universe   TEXT NOT NULL,
                    scanned_at TEXT NOT NULL,
                    symbol     TEXT NOT NULL,
                    provider   TEXT,
                    action     TEXT,
                    score      REAL,
                    json       TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_scan_universe
                    ON scan_results (universe, scanned_at);
                """
            )

    # --- Snapshot cache -------------------------------------------------
    @staticmethod
    def _snapshot_key(symbol: str, cache_key: str | None) -> str:
        # Keep the public symbol separate from the context used to avoid reusing a snapshot
        # produced by a different source, history window, or macro/news configuration.
        return f"{symbol}::{cache_key}" if cache_key else symbol

    def get_snapshot(
        self, symbol: str, ttl_hours: float, *, cache_key: str | None = None
    ) -> StockSnapshot | None:
        storage_key = self._snapshot_key(symbol, cache_key)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT as_of, json FROM snapshots WHERE symbol = ?", (storage_key,)
            ).fetchone()
        if not row:
            return None
        as_of, payload = row
        if _now() - _parse(as_of) > timedelta(hours=ttl_hours):
            return None
        try:
            return StockSnapshot.model_validate_json(payload)
        except Exception:
            return None

    def put_snapshot(self, snapshot: StockSnapshot, *, cache_key: str | None = None) -> None:
        storage_key = self._snapshot_key(snapshot.symbol, cache_key)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO snapshots (symbol, as_of, json) VALUES (?, ?, ?)",
                (storage_key, _now().isoformat(), snapshot.model_dump_json()),
            )

    # --- Constituent cache ----------------------------------------------
    def get_constituents(
        self, universe: str, ttl_days: float
    ) -> tuple[list[Constituent], bool] | None:
        """Return (constituents, is_fresh) or None if nothing cached.

        `is_fresh` is False when the cache exists but is older than ttl_days; callers can
        still use the stale copy when no local refresh is available.
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT symbol, name, sector, fetched_at FROM constituents "
                "WHERE universe = ? ORDER BY rowid",
                (universe,),
            ).fetchall()
        if not rows:
            return None
        fetched_at = _parse(rows[0][3])
        is_fresh = _now() - fetched_at <= timedelta(days=ttl_days)
        members = [Constituent(symbol=s, name=n, sector=sec) for s, n, sec, _ in rows]
        return members, is_fresh

    def put_constituents(self, universe: str, members: list[Constituent]) -> None:
        ts = _now().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM constituents WHERE universe = ?", (universe,))
            conn.executemany(
                "INSERT INTO constituents (universe, symbol, name, sector, fetched_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [(universe, m.symbol, m.name, m.sector, ts) for m in members],
            )

    # --- Scan history ---------------------------------------------------
    def save_scan(self, result: ScanResult) -> str:
        """Persist every row of a scan under a new scan_id (returned)."""
        scan_id = uuid.uuid4().hex
        scanned_at = result.scanned_at.isoformat()
        with self._lock, self._connect() as conn:
            conn.executemany(
                "INSERT INTO scan_results "
                "(scan_id, universe, scanned_at, symbol, provider, action, score, json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        scan_id,
                        result.universe,
                        scanned_at,
                        r.symbol,
                        r.provider,
                        r.action.value if r.action else None,
                        r.score,
                        r.model_dump_json(),
                    )
                    for r in result.rows
                ],
            )
        return scan_id

    def latest_scan(self, universe: str, before: str | None = None) -> list[ScreenRow]:
        """Rows from the most recent saved scan of `universe` (optionally before a scan_id)."""
        with self._lock, self._connect() as conn:
            if before:
                sid_row = conn.execute(
                    "SELECT scanned_at FROM scan_results WHERE scan_id = ? LIMIT 1", (before,)
                ).fetchone()
                cutoff = sid_row[0] if sid_row else None
                sid = conn.execute(
                    "SELECT scan_id FROM scan_results "
                    "WHERE universe = ? AND scanned_at < ? "
                    "ORDER BY scanned_at DESC LIMIT 1",
                    (universe, cutoff),
                ).fetchone()
            else:
                sid = conn.execute(
                    "SELECT scan_id FROM scan_results WHERE universe = ? "
                    "ORDER BY scanned_at DESC LIMIT 1",
                    (universe,),
                ).fetchone()
            if not sid:
                return []
            rows = conn.execute(
                "SELECT json FROM scan_results WHERE scan_id = ?", (sid[0],)
            ).fetchall()
        return [ScreenRow.model_validate_json(j) for (j,) in rows]

    def diff_scans(self, universe: str) -> list[dict]:
        """Compare the two most recent scans of a universe.

        Returns per-symbol deltas: {symbol, old_action, new_action, old_score, new_score,
        score_delta, changed}. Only symbols present in the newest scan are reported.
        """
        current = self.latest_scan(universe)
        if not current:
            return []
        # Find the scan_id of the current scan to look up the one before it.
        with self._lock, self._connect() as conn:
            sid = conn.execute(
                "SELECT scan_id FROM scan_results WHERE universe = ? "
                "ORDER BY scanned_at DESC LIMIT 1",
                (universe,),
            ).fetchone()
        previous = self.latest_scan(universe, before=sid[0]) if sid else []
        prev_by_sym = {r.symbol: r for r in previous}

        out: list[dict] = []
        for row in current:
            prev = prev_by_sym.get(row.symbol)
            old_action = prev.action.value if prev and prev.action else None
            new_action = row.action.value if row.action else None
            old_score = prev.score if prev else None
            delta = (
                round(row.score - old_score, 1)
                if (row.score is not None and old_score is not None)
                else None
            )
            out.append(
                {
                    "symbol": row.symbol,
                    "old_action": old_action,
                    "new_action": new_action,
                    "old_score": old_score,
                    "new_score": row.score,
                    "score_delta": delta,
                    "changed": old_action != new_action,
                }
            )
        return out
