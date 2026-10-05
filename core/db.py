from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "monitor.db"


@contextmanager
def connect():
    """Open a connection, commit on success, always close.

    The engine and the UI run as separate processes, so WAL mode and a
    generous busy timeout keep them from blocking each other.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS minute_bars (
    symbol TEXT NOT NULL,
    ts TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(symbol, ts)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    start_ts TEXT NOT NULL,
    last_ts TEXT NOT NULL,
    event_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    return_5m REAL,
    return_15m REAL,
    volume_ratio_5m REAL,
    price_z REAL,
    volume_z REAL,
    peak_return_5m REAL,
    peak_ts TEXT,
    peak_volume_ratio_5m REAL,
    status TEXT NOT NULL DEFAULT 'open',
    detection_rule TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS system_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pr_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    published_ts TEXT NOT NULL,
    url TEXT,
    pr_type TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 원인 분석용
CREATE TABLE IF NOT EXISTS trader_snapshots (      -- 거래원(증권사 창구) 당일 누적, 1분마다
    symbol TEXT NOT NULL,
    ts TEXT NOT NULL,
    trader_no TEXT NOT NULL,
    name TEXT,
    buy INTEGER,
    sell INTEGER,
    is_foreign INTEGER,
    PRIMARY KEY(symbol, ts, trader_no)
);
CREATE TABLE IF NOT EXISTS news (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,          -- 이 기사가 태그된 종목
    ts TEXT NOT NULL,
    office TEXT, title TEXT, body TEXT, url TEXT, cluster INTEGER,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS disclosures (
    no TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    title TEXT, kind TEXT, summary TEXT,
    first_seen TEXT                -- 감시 엔진이 처음 본 시각 (공시 시각 참고용)
);
CREATE TABLE IF NOT EXISTS investor_daily (
    symbol TEXT NOT NULL,
    bizdate TEXT NOT NULL,
    foreign_net INTEGER, organ_net INTEGER, individual_net INTEGER,
    close REAL, volume INTEGER, foreign_ratio REAL,
    PRIMARY KEY(symbol, bizdate)
);
CREATE TABLE IF NOT EXISTS trader_daily (          -- 장 마감 시점 거래원 (출처별), 같은 업종 종목 포함
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    source TEXT NOT NULL,
    name TEXT NOT NULL,
    buy INTEGER, sell INTEGER, is_foreign INTEGER, buy_top5 INTEGER, sell_top5 INTEGER,
    captured_at TEXT,
    PRIMARY KEY(symbol, date, source, name)
);
CREATE TABLE IF NOT EXISTS broker_side_daily (     -- 다음 금융: 외국계/국내 창구 일별 합계 (추정치)
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    foreign_buy INTEGER, foreign_sell INTEGER, foreign_net INTEGER,
    domestic_buy INTEGER, domestic_sell INTEGER, domestic_net INTEGER,
    PRIMARY KEY(symbol, date)
);
CREATE TABLE IF NOT EXISTS research (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    broker TEXT, title TEXT, date TEXT, url TEXT
);
CREATE TABLE IF NOT EXISTS related_stocks (
    code TEXT PRIMARY KEY,
    name TEXT, market TEXT, source TEXT, updated_at TEXT
);

-- 이 종목이 다뤄진 기사 전체 (회사 발표·언론 분석·리서치·테마·시황)
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    ts TEXT NOT NULL,
    office TEXT, title TEXT, body TEXT, url TEXT,
    outlets INTEGER DEFAULT 1,      -- 같은 내용을 낸 매체 수 (네이버 묶음)
    category TEXT,                  -- 회사 발표 / 언론 분석 / 리서치 / 테마·수혜주 / 시황·특징주 / 단순 언급
    tone INTEGER DEFAULT 0,         -- +1 호재 / -1 악재 / 0 중립
    mention TEXT,                   -- 제목 / 본문 / 연결
    story TEXT,                     -- 같은 내용 기사 묶음 (가장 먼저 나온 기사 id)
    first_seen TEXT,
    hidden INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_articles_ts ON articles(symbol, ts);

-- 일봉 (기사 당일·다음날 반응 계산용, 이 종목·KOSDAQ·같은 업종)
CREATE TABLE IF NOT EXISTS daily_bars (
    symbol TEXT NOT NULL, date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume INTEGER,
    PRIMARY KEY(symbol, date)
);

-- 시장 전체 뉴스 (거시·정치·지정학 단서)
CREATE TABLE IF NOT EXISTS macro_news (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    office TEXT, title TEXT, body TEXT, url TEXT,
    topics TEXT,                    -- 해당하는 주제 (쉼표 구분)
    first_seen TEXT
);
CREATE INDEX IF NOT EXISTS idx_macro_ts ON macro_news(ts);

-- 테마 (네이버 테마 분류)
CREATE TABLE IF NOT EXISTS stock_themes (
    code TEXT NOT NULL, theme_no INTEGER NOT NULL, theme_name TEXT, reason TEXT, updated_at TEXT,
    PRIMARY KEY(code, theme_no)
);
CREATE TABLE IF NOT EXISTS theme_snap (            -- 장중 5분마다 (이 종목 테마 + 상위 테마)
    ts TEXT NOT NULL, theme_no INTEGER NOT NULL, name TEXT,
    change_rate REAL, rank INTEGER, n_themes INTEGER, rise INTEGER, total INTEGER,
    PRIMARY KEY(ts, theme_no)
);
CREATE TABLE IF NOT EXISTS theme_daily (
    date TEXT NOT NULL, theme_no INTEGER NOT NULL, name TEXT,
    change_rate REAL, rank INTEGER, n_themes INTEGER, rise INTEGER, total INTEGER,
    leaders TEXT, mine INTEGER DEFAULT 0, updated_at TEXT,
    PRIMARY KEY(date, theme_no)
);

-- 해외 지표 (미국 방산주, 유가) 일별
CREATE TABLE IF NOT EXISTS overseas_daily (
    sym TEXT NOT NULL, date TEXT NOT NULL, name TEXT, close REAL, chg REAL,
    PRIMARY KEY(sym, date)
);

CREATE INDEX IF NOT EXISTS idx_bars_symbol_ts ON minute_bars(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_news_ts ON news(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_trader_ts ON trader_snapshots(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_events_symbol_ts ON events(symbol, start_ts);
"""

# Columns added after the first MVP; older DB files get them via ALTER TABLE.
_EVENT_MIGRATIONS = {
    "peak_return_5m": "REAL",
    "peak_ts": "TEXT",
    "peak_volume_ratio_5m": "REAL",
    "cause_json": "TEXT",
    "cause_stage": "TEXT",
    "cause_updated_at": "TEXT",
    "trade_value": "REAL",
    "tier": "TEXT",
}


_SNAPSHOT_MIGRATIONS = {"buy_top5": "INTEGER", "sell_top5": "INTEGER"}


def init_db():
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        scols = {r["name"] for r in conn.execute("PRAGMA table_info(trader_snapshots)")}
        for col, typ in _SNAPSHOT_MIGRATIONS.items():
            if col not in scols:
                conn.execute(f"ALTER TABLE trader_snapshots ADD COLUMN {col} {typ}")
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(events)")}
        for col, typ in _EVENT_MIGRATIONS.items():
            if col not in cols:
                conn.execute(f"ALTER TABLE events ADD COLUMN {col} {typ}")
        # v1.1부터 '거래량만 급증'은 이벤트로 기록하지 않음 → 예전 기록도 정리
        conn.execute("DELETE FROM events WHERE direction='flat'")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def set_state(key: str, value: str):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO system_state(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at
            """,
            (key, value, now_iso()),
        )


def get_state(key: str):
    with connect() as conn:
        row = conn.execute("SELECT * FROM system_state WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None


def count_bars(symbol: str) -> int:
    with connect() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM minute_bars WHERE symbol=?", (symbol,)
            ).fetchone()[0]
        )


def clear_demo_data(symbol: str):
    with connect() as conn:
        conn.execute("DELETE FROM events WHERE symbol=?", (symbol,))
        conn.execute("DELETE FROM minute_bars WHERE symbol=?", (symbol,))
        conn.execute(
            "DELETE FROM system_state WHERE key=?", (f"last_processed_ts:{symbol}",)
        )
