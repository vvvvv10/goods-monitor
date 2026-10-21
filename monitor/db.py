"""SQLite 存储层：价格历史、已见商品（关键词去重）、提醒记录（冷却控制）。"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional, Union

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    platform  TEXT NOT NULL,
    item_id   TEXT NOT NULL,
    price     REAL,
    stock     INTEGER,
    title     TEXT,
    url       TEXT,
    ts        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_price_history_item
    ON price_history(platform, item_id, ts DESC);

CREATE TABLE IF NOT EXISTS seen_items (
    platform  TEXT NOT NULL,
    item_id   TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    title     TEXT,
    price     REAL,
    url       TEXT,
    ts        REAL NOT NULL,
    PRIMARY KEY (platform, item_id, rule_name)
);

CREATE TABLE IF NOT EXISTS alerts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    dedup_key TEXT NOT NULL,
    ts        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_key ON alerts(dedup_key, ts DESC);
"""


class DB:
    def __init__(self, path: Union[str, Path]):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(p), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self.conn:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)

    # ---------- 价格历史 ----------

    def record_price(self, platform: str, item_id: str, price: Optional[float],
                     title: str = "", url: str = "", stock: Optional[bool] = None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO price_history(platform,item_id,price,stock,title,url,ts)"
                " VALUES(?,?,?,?,?,?,?)",
                (platform, item_id, price,
                 None if stock is None else int(stock), title, url, time.time()),
            )

    def last_record(self, platform: str, item_id: str) -> Optional[sqlite3.Row]:
        cur = self.conn.execute(
            "SELECT * FROM price_history WHERE platform=? AND item_id=?"
            " ORDER BY ts DESC LIMIT 1",
            (platform, item_id),
        )
        return cur.fetchone()

    def history(self, platform: str, item_id: str, limit: int = 20) -> list:
        cur = self.conn.execute(
            "SELECT * FROM price_history WHERE platform=? AND item_id=?"
            " ORDER BY ts DESC LIMIT ?",
            (platform, item_id, limit),
        )
        return cur.fetchall()

    # ---------- 关键词规则去重 ----------

    def is_seen(self, platform: str, item_id: str, rule_name: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM seen_items WHERE platform=? AND item_id=? AND rule_name=?",
            (platform, item_id, rule_name),
        )
        return cur.fetchone() is not None

    def mark_seen(self, platform: str, item_id: str, rule_name: str,
                  title: str = "", price: Optional[float] = None, url: str = "") -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO seen_items"
                "(platform,item_id,rule_name,title,price,url,ts) VALUES(?,?,?,?,?,?,?)",
                (platform, item_id, rule_name, title, price, url, time.time()),
            )

    def seen_count(self, rule_name: Optional[str] = None) -> int:
        if rule_name:
            cur = self.conn.execute(
                "SELECT COUNT(*) AS c FROM seen_items WHERE rule_name=?", (rule_name,))
        else:
            cur = self.conn.execute("SELECT COUNT(*) AS c FROM seen_items")
        return cur.fetchone()["c"]

    # ---------- 提醒冷却 ----------

    def alert_sent_recently(self, dedup_key: str, cooldown_seconds: float) -> bool:
        cur = self.conn.execute(
            "SELECT ts FROM alerts WHERE dedup_key=? ORDER BY ts DESC LIMIT 1",
            (dedup_key,),
        )
        row = cur.fetchone()
        return bool(row) and (time.time() - row["ts"]) < cooldown_seconds

    def log_alert(self, dedup_key: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO alerts(dedup_key, ts) VALUES(?,?)",
                (dedup_key, time.time()),
            )

    def alerts_count(self, since_seconds: float = 86400) -> int:
        cur = self.conn.execute(
            "SELECT COUNT(*) AS c FROM alerts WHERE ts >= ?",
            (time.time() - since_seconds,),
        )
        return cur.fetchone()["c"]

    # ---------- 概览 ----------

    def latest_items(self, limit: int = 30) -> list:
        cur = self.conn.execute(
            """SELECT platform, item_id, title, url, price, stock, ts
               FROM price_history
               WHERE id IN (SELECT MAX(id) FROM price_history GROUP BY platform, item_id)
               ORDER BY ts DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()
