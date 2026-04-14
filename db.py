import sqlite3
import json
import time

conn = sqlite3.connect("economy.db", check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA foreign_keys=ON")
cursor = conn.cursor()

cursor.executescript("""
CREATE TABLE IF NOT EXISTS citizens (
    user_id     INTEGER PRIMARY KEY,
    cash        REAL    DEFAULT 1000.0,
    bank        REAL    DEFAULT 0.0,
    credit_score INTEGER DEFAULT 650,
    skill_level  INTEGER DEFAULT 1,
    education   TEXT    DEFAULT 'none',
    happiness   REAL    DEFAULT 75.0,
    job_id      TEXT    DEFAULT NULL,
    job_xp      INTEGER DEFAULT 0,
    last_work   INTEGER DEFAULT 0,
    last_daily  INTEGER DEFAULT 0,
    debt        REAL    DEFAULT 0.0,
    housing     TEXT    DEFAULT 'renting',
    last_expense INTEGER DEFAULT 0,
    registered_at INTEGER DEFAULT 0,
    is_jailed   INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS businesses (
    biz_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id     INTEGER NOT NULL,
    name         TEXT    UNIQUE NOT NULL,
    type         TEXT    NOT NULL,
    cash         REAL    DEFAULT 5000.0,
    revenue      REAL    DEFAULT 0.0,
    expenses     REAL    DEFAULT 0.0,
    reputation   INTEGER DEFAULT 50,
    employees    INTEGER DEFAULT 0,
    shares_issued INTEGER DEFAULT 0,
    share_price  REAL    DEFAULT 10.0,
    founded_at   INTEGER DEFAULT 0,
    is_bankrupt  INTEGER DEFAULT 0,
    is_public    INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS market_goods (
    good_id      TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    base_price   REAL NOT NULL,
    current_price REAL NOT NULL,
    supply       INTEGER DEFAULT 1000,
    demand       INTEGER DEFAULT 500,
    volatility   REAL    DEFAULT 0.05
);

CREATE TABLE IF NOT EXISTS inventories (
    user_id  INTEGER,
    good_id  TEXT,
    quantity INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, good_id)
);

CREATE TABLE IF NOT EXISTS market_listings (
    listing_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id     INTEGER,
    good_id       TEXT,
    quantity      INTEGER,
    price_per_unit REAL,
    listed_at     INTEGER
);

CREATE TABLE IF NOT EXISTS loans (
    loan_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    borrower_id   INTEGER,
    principal     REAL,
    remaining     REAL,
    interest_rate REAL,
    weekly_payment REAL,
    issued_at     INTEGER,
    last_payment  INTEGER DEFAULT 0,
    status        TEXT    DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS portfolios (
    user_id       INTEGER,
    biz_id        INTEGER,
    shares        INTEGER DEFAULT 0,
    avg_buy_price REAL    DEFAULT 0.0,
    PRIMARY KEY (user_id, biz_id)
);

CREATE TABLE IF NOT EXISTS transactions (
    tx_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    tx_type     TEXT,
    amount      REAL,
    description TEXT,
    timestamp   INTEGER
);

CREATE TABLE IF NOT EXISTS economy_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS active_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    description TEXT,
    effects     TEXT,
    started_at  INTEGER,
    ends_at     INTEGER
);

CREATE TABLE IF NOT EXISTS government (
    key   TEXT PRIMARY KEY,
    value REAL DEFAULT 0.0
);
""")

conn.commit()


def _seed_economy_state():
    defaults = {
        "inflation_rate": "0.02",
        "base_interest_rate": "0.05",
        "gdp": "0.0",
        "unemployment_rate": "0.0",
        "economic_phase": "stable",
        "total_money_supply": "0.0",
        "min_wage": "50.0",
        "last_simulation": str(int(time.time())),
    }
    for k, v in defaults.items():
        cursor.execute("INSERT OR IGNORE INTO economy_state(key, value) VALUES (?, ?)", (k, v))

    gov_defaults = {"revenue": 0.0, "expenses": 0.0, "reserves": 50000.0}
    for k, v in gov_defaults.items():
        cursor.execute("INSERT OR IGNORE INTO government(key, value) VALUES (?, ?)", (k, v))
    conn.commit()


def _seed_market_goods():
    goods = [
        ("bread",       "Bread",          "food",      12.0,   0.04),
        ("meat",        "Meat",           "food",      45.0,   0.06),
        ("vegetables",  "Vegetables",     "food",      18.0,   0.05),
        ("coffee",      "Coffee",         "food",      25.0,   0.05),
        ("alcohol",     "Alcohol",        "food",      60.0,   0.07),
        ("steel",       "Steel",          "materials", 80.0,   0.06),
        ("wood",        "Wood",           "materials", 30.0,   0.05),
        ("plastic",     "Plastic",        "materials", 20.0,   0.04),
        ("concrete",    "Concrete",       "materials", 35.0,   0.04),
        ("copper",      "Copper",         "materials", 95.0,   0.07),
        ("chips",       "Microchips",     "tech",      200.0,  0.09),
        ("batteries",   "Batteries",      "tech",      55.0,   0.06),
        ("phones",      "Smartphones",    "tech",      450.0,  0.08),
        ("computers",   "Computers",      "tech",      900.0,  0.07),
        ("software",    "Software",       "tech",      300.0,  0.06),
        ("oil",         "Oil (barrel)",   "energy",    70.0,   0.10),
        ("coal",        "Coal",           "energy",    40.0,   0.07),
        ("solar",       "Solar Panels",   "energy",    250.0,  0.06),
        ("jewelry",     "Jewelry",        "luxury",    500.0,  0.10),
        ("art",         "Fine Art",       "luxury",    1200.0, 0.15),
    ]
    for good_id, name, category, price, vol in goods:
        cursor.execute(
            "INSERT OR IGNORE INTO market_goods(good_id, name, category, base_price, current_price, volatility) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (good_id, name, category, price, price, vol)
        )
    conn.commit()


_seed_economy_state()
_seed_market_goods()
