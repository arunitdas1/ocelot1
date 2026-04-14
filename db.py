import sqlite3
import time
import threading
from contextlib import contextmanager

conn = sqlite3.connect("economy.db", check_same_thread=False)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA foreign_keys=ON")
conn.execute("PRAGMA busy_timeout=5000")
cursor = conn.cursor()
_db_write_lock = threading.RLock()


@contextmanager
def write_txn():
    """
    Serialize multi-step writes so related balance/state mutations are atomic.
    """
    with _db_write_lock:
        try:
            yield
            conn.commit()
        except Exception:
            conn.rollback()
            raise

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

def _get_user_version() -> int:
    cursor.execute("PRAGMA user_version")
    return int(cursor.fetchone()[0])


def _set_user_version(v: int):
    cursor.execute(f"PRAGMA user_version = {int(v)}")


def _column_exists(table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(r[1] == column for r in cursor.fetchall())


def _ensure_column(table: str, column: str, ddl_type_and_default: str):
    if not _column_exists(table, column):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type_and_default}")


def _apply_migrations():
    """
    Lightweight, SQLite-native migrations using PRAGMA user_version.
    Additive-only upgrades to keep existing worlds running.
    """
    v = _get_user_version()
    try:
        if v < 1:
            # Citizens: realism extensions
            _ensure_column("citizens", "lifestyle_tier", "TEXT DEFAULT 'standard'")
            _ensure_column("citizens", "debt_stress_score", "REAL DEFAULT 0.0")
            _ensure_column("citizens", "criminal_record_points", "INTEGER DEFAULT 0")
            _ensure_column("citizens", "wanted_level", "INTEGER DEFAULT 0")
            _ensure_column("citizens", "last_release_at", "INTEGER DEFAULT 0")

            # Macro snapshots
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS macro_snapshots (
                ts INTEGER PRIMARY KEY,
                inflation REAL,
                base_interest REAL,
                unemployment REAL,
                gdp_proxy REAL,
                money_supply REAL,
                velocity_proxy REAL,
                avg_credit_score REAL,
                gov_reserves REAL,
                active_loans INTEGER,
                active_businesses INTEGER,
                bankrupt_businesses INTEGER,
                defaults_last_7d INTEGER
            )
            """)

            # Trust / reputation
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS trust_edges (
                src_user_id INTEGER,
                dst_user_id INTEGER,
                trust_score REAL DEFAULT 0.0,
                interactions INTEGER DEFAULT 0,
                updated_at INTEGER DEFAULT 0,
                PRIMARY KEY (src_user_id, dst_user_id)
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS reputation_ledger (
                rep_id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                delta REAL NOT NULL,
                reason TEXT,
                source_type TEXT,
                source_id TEXT,
                created_at INTEGER DEFAULT 0
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rep_ledger_entity ON reputation_ledger(entity_type, entity_id, created_at)")

            # Insurance
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS insurance_policies (
                policy_id INTEGER PRIMARY KEY AUTOINCREMENT,
                holder_id INTEGER NOT NULL,
                policy_type TEXT NOT NULL,
                premium REAL NOT NULL,
                coverage_limit REAL NOT NULL,
                deductible REAL NOT NULL,
                risk_score REAL DEFAULT 1.0,
                status TEXT DEFAULT 'active',
                started_at INTEGER DEFAULT 0,
                ends_at INTEGER DEFAULT 0,
                last_billed_at INTEGER DEFAULT 0
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_insurance_holder_status ON insurance_policies(holder_id, status)")
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS insurance_claims (
                claim_id INTEGER PRIMARY KEY AUTOINCREMENT,
                policy_id INTEGER NOT NULL,
                claimant_id INTEGER NOT NULL,
                incident_type TEXT NOT NULL,
                claim_amount REAL NOT NULL,
                approved_amount REAL DEFAULT 0.0,
                status TEXT DEFAULT 'filed',
                filed_at INTEGER DEFAULT 0,
                resolved_at INTEGER DEFAULT 0
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_claims_policy ON insurance_claims(policy_id, status)")

            # Contracts
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS contracts (
                contract_id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_type TEXT NOT NULL,
                party_a_type TEXT NOT NULL,
                party_a_id INTEGER NOT NULL,
                party_b_type TEXT NOT NULL,
                party_b_id INTEGER NOT NULL,
                terms_json TEXT NOT NULL,
                value REAL DEFAULT 0.0,
                collateral_json TEXT DEFAULT '{}',
                start_at INTEGER DEFAULT 0,
                end_at INTEGER DEFAULT 0,
                status TEXT DEFAULT 'draft',
                created_at INTEGER DEFAULT 0,
                signed_at INTEGER DEFAULT 0,
                last_event_at INTEGER DEFAULT 0
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_contracts_party_a ON contracts(party_a_type, party_a_id, status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_contracts_party_b ON contracts(party_b_type, party_b_id, status)")
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS contract_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT DEFAULT '{}',
                created_at INTEGER DEFAULT 0
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_contract_events_contract ON contract_events(contract_id, created_at)")

            # Crime / enforcement
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS offenses (
                offense_id INTEGER PRIMARY KEY AUTOINCREMENT,
                offender_id INTEGER NOT NULL,
                offense_type TEXT NOT NULL,
                severity INTEGER DEFAULT 1,
                victim_type TEXT,
                victim_id INTEGER,
                fine_amount REAL DEFAULT 0.0,
                jail_seconds INTEGER DEFAULT 0,
                detected_prob_snapshot REAL DEFAULT 0.0,
                committed_at INTEGER DEFAULT 0
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_offenses_offender_time ON offenses(offender_id, committed_at)")
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS enforcement_actions (
                action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                offense_id INTEGER NOT NULL,
                action_type TEXT NOT NULL,
                penalty_cash REAL DEFAULT 0.0,
                penalty_jail INTEGER DEFAULT 0,
                bribe_amount REAL,
                resolved_at INTEGER DEFAULT 0
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_enforcement_offense ON enforcement_actions(offense_id)")

            # Housing tiers
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS housing_units (
                unit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                tier TEXT NOT NULL,
                base_rent REAL NOT NULL,
                upkeep REAL NOT NULL,
                comfort REAL NOT NULL,
                supply INTEGER DEFAULT 1000
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_housing_tier ON housing_units(tier)")
            cursor.execute("SELECT COUNT(*) FROM housing_units")
            if cursor.fetchone()[0] == 0:
                tiers = [
                    ("homeless", 0.0, 0.0, 0.2, 999999),
                    ("budget", 120.0, 10.0, 0.5, 2500),
                    ("standard", 200.0, 20.0, 0.7, 2000),
                    ("premium", 350.0, 35.0, 0.85, 1200),
                    ("luxury", 600.0, 60.0, 0.95, 600),
                ]
                cursor.executemany(
                    "INSERT INTO housing_units(tier, base_rent, upkeep, comfort, supply) VALUES (?, ?, ?, ?, ?)",
                    tiers
                )

            # Labor market
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS labor_openings (
                opening_id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                category TEXT,
                min_skill INTEGER DEFAULT 1,
                min_edu TEXT DEFAULT 'none',
                wage_min REAL DEFAULT 0.0,
                wage_max REAL DEFAULT 0.0,
                slots INTEGER DEFAULT 1,
                created_at INTEGER DEFAULT 0,
                expires_at INTEGER DEFAULT 0,
                status TEXT DEFAULT 'open'
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_openings_status ON labor_openings(status, expires_at)")
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS employment_history (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                job_id TEXT,
                details TEXT,
                created_at INTEGER DEFAULT 0
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_employment_user_time ON employment_history(user_id, created_at)")

            # Supply chain state
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS supply_chain_state (
                category TEXT PRIMARY KEY,
                backlog REAL DEFAULT 0.0,
                inventory REAL DEFAULT 0.0,
                updated_at INTEGER DEFAULT 0
            )
            """)
            for cat in ("food", "materials", "tech", "energy", "luxury"):
                cursor.execute(
                    "INSERT OR IGNORE INTO supply_chain_state(category, backlog, inventory, updated_at) VALUES (?, 0.0, 0.0, ?)",
                    (cat, int(time.time()))
                )

            policy_defaults = {
                "consumer_confidence": "0.5",
                "business_confidence": "0.5",
                "policy_monetary_stance": "0.0",
                "policy_fiscal_stance": "0.0",
                "seasonality_strength": "0.15",
            }
            for k, v0 in policy_defaults.items():
                cursor.execute("INSERT OR IGNORE INTO economy_state(key, value) VALUES (?, ?)", (k, v0))

            _set_user_version(1)
            conn.commit()
            v = 1

        if v < 2:
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS admin_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                details TEXT,
                created_at INTEGER DEFAULT 0
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_admin_audit_time ON admin_audit(created_at)")

            extra_defaults = {
                "maintenance_mode": "0",
                "economy_frozen": "0",
                "events_enabled": "1",
                "global_money_multiplier": "1.0",
                "global_xp_multiplier": "1.0",
            }
            for k, val in extra_defaults.items():
                cursor.execute("INSERT OR IGNORE INTO economy_state(key, value) VALUES (?, ?)", (k, val))

            _set_user_version(2)
            conn.commit()
            v = 2

        if v < 3:
            # Quests + streaks
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS quests_daily (
                quest_id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_value REAL NOT NULL,
                reward_cash REAL DEFAULT 0.0,
                reward_xp INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS quests_weekly (
                quest_id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                target_type TEXT NOT NULL,
                target_value REAL NOT NULL,
                reward_cash REAL DEFAULT 0.0,
                reward_xp INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_quests (
                user_id INTEGER NOT NULL,
                quest_type TEXT NOT NULL,
                quest_key TEXT NOT NULL,
                progress REAL DEFAULT 0.0,
                target REAL DEFAULT 1.0,
                claimed INTEGER DEFAULT 0,
                assigned_at INTEGER DEFAULT 0,
                resets_at INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, quest_type, quest_key)
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_user_quests_reset ON user_quests(user_id, resets_at)")
            _ensure_column("citizens", "daily_streak", "INTEGER DEFAULT 0")
            _ensure_column("citizens", "last_streak_claim", "INTEGER DEFAULT 0")
            _ensure_column("citizens", "streak_protect_tokens", "INTEGER DEFAULT 0")

            # Event hub
            _ensure_column("active_events", "tag", "TEXT DEFAULT ''")
            _ensure_column("active_events", "reward_pool", "REAL DEFAULT 0.0")
            _ensure_column("active_events", "max_participants", "INTEGER DEFAULT 0")
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS event_participants (
                event_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                points REAL DEFAULT 0.0,
                joined_at INTEGER DEFAULT 0,
                claimed INTEGER DEFAULT 0,
                PRIMARY KEY (event_id, user_id)
            )
            """)

            # Achievements / collections
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS achievements (
                ach_key TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                metric_key TEXT NOT NULL,
                target_value REAL NOT NULL,
                reward_cash REAL DEFAULT 0.0,
                reward_badge TEXT DEFAULT ''
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_achievements (
                user_id INTEGER NOT NULL,
                ach_key TEXT NOT NULL,
                progress REAL DEFAULT 0.0,
                unlocked INTEGER DEFAULT 0,
                claimed INTEGER DEFAULT 0,
                unlocked_at INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, ach_key)
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS collections (
                user_id INTEGER NOT NULL,
                collection_key TEXT NOT NULL,
                item_key TEXT NOT NULL,
                obtained_at INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, collection_key, item_key)
            )
            """)

            # Seasonal ladders
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS season_meta (
                season_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                starts_at INTEGER NOT NULL,
                ends_at INTEGER NOT NULL,
                status TEXT DEFAULT 'active'
            )
            """)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS season_stats (
                season_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                net_worth REAL DEFAULT 0.0,
                trade_volume REAL DEFAULT 0.0,
                work_shifts INTEGER DEFAULT 0,
                quests_completed INTEGER DEFAULT 0,
                updated_at INTEGER DEFAULT 0,
                PRIMARY KEY (season_id, user_id)
            )
            """)

            # Reminder preferences
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS reminder_prefs (
                user_id INTEGER PRIMARY KEY,
                dm_enabled INTEGER DEFAULT 0,
                daily_ready INTEGER DEFAULT 1,
                work_ready INTEGER DEFAULT 1,
                quest_ready INTEGER DEFAULT 1,
                updated_at INTEGER DEFAULT 0
            )
            """)

            # Retention metrics
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS retention_metrics (
                metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
                day_key TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                metric_value REAL DEFAULT 0.0,
                created_at INTEGER DEFAULT 0
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_retention_metrics_day ON retention_metrics(day_key, metric_name)")

            # Seed quest / achievement catalogs
            daily_seed = [
                ("daily_work_3", "Shift Worker", "Complete 3 work shifts today", "work_count", 3, 250.0, 25),
                ("daily_trade_5", "Trader", "Complete 5 market actions today", "trade_count", 5, 200.0, 20),
                ("daily_save_500", "Saver", "Increase bank balance by $500 today", "bank_gain", 500, 180.0, 15),
            ]
            weekly_seed = [
                ("weekly_work_15", "Workhorse", "Complete 15 shifts this week", "work_count", 15, 1200.0, 100),
                ("weekly_trade_20", "Market Maker", "Complete 20 trade actions this week", "trade_count", 20, 1000.0, 80),
            ]
            cursor.executemany(
                "INSERT OR IGNORE INTO quests_daily(key, title, description, target_type, target_value, reward_cash, reward_xp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                daily_seed,
            )
            cursor.executemany(
                "INSERT OR IGNORE INTO quests_weekly(key, title, description, target_type, target_value, reward_cash, reward_xp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                weekly_seed,
            )
            ach_seed = [
                ("ach_networth_10k", "Five Digits", "Reach $10,000 net worth", "net_worth", 10000, 500, "Bronze Saver"),
                ("ach_trade_100", "Floor Veteran", "Complete 100 trades", "trade_count", 100, 750, "Market Veteran"),
                ("ach_work_200", "Career Grinder", "Complete 200 shifts", "work_count", 200, 900, "Work Legend"),
            ]
            cursor.executemany(
                "INSERT OR IGNORE INTO achievements(ach_key, title, description, metric_key, target_value, reward_cash, reward_badge) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ach_seed,
            )

            # Ensure one active season exists
            cursor.execute("SELECT COUNT(*) FROM season_meta WHERE status = 'active'")
            if cursor.fetchone()[0] == 0:
                now = int(time.time())
                cursor.execute(
                    "INSERT INTO season_meta(name, starts_at, ends_at, status) VALUES (?, ?, ?, 'active')",
                    (f"Season {time.strftime('%Y-%m')}", now, now + 30 * 86400),
                )

            _set_user_version(3)
            conn.commit()
    except Exception:
        conn.rollback()
        raise


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
_apply_migrations()
