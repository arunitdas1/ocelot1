import os
import threading
import time
import uuid
from contextlib import contextmanager

from pymongo import ASCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise RuntimeError("MONGO_URI is required for MongoDB connection")

_client = MongoClient(MONGO_URI, retryWrites=True)
db = _client["ocelot"]

# =========================
# 🧪 MONGO CONNECTION CHECK (ADDED ONLY)
# =========================
def test_connection():
    try:
        result = _client.admin.command("ping")
        print("✅ MongoDB PING SUCCESS:", result)
        return True
    except Exception as e:
        print("❌ MongoDB CONNECTION FAILED:", e)
        return False


citizens = db["citizens"]
businesses = db["businesses"]
market_goods = db["market_goods"]
inventories = db["inventories"]
market_listings = db["market_listings"]
loans = db["loans"]
portfolios = db["portfolios"]
transactions = db["transactions"]
economy_state = db["economy_state"]
active_events = db["active_events"]
government = db["government"]
supply_chain_state = db["supply_chain_state"]
contracts = db["contracts"]
contract_events = db["contract_events"]
offenses = db["offenses"]
enforcement_actions = db["enforcement_actions"]
insurance_policies = db["insurance_policies"]
insurance_claims = db["insurance_claims"]
housing_units = db["housing_units"]
labor_openings = db["labor_openings"]
employment_history = db["employment_history"]
admin_audit = db["admin_audit"]
event_participants = db["event_participants"]
quests_daily = db["quests_daily"]
quests_weekly = db["quests_weekly"]
user_quests = db["user_quests"]
achievements = db["achievements"]
user_achievements = db["user_achievements"]
collections = db["collections"]
season_meta = db["season_meta"]
season_stats = db["season_stats"]
reminder_prefs = db["reminder_prefs"]
retention_metrics = db["retention_metrics"]
macro_snapshots = db["macro_snapshots"]
trust_edges = db["trust_edges"]
reputation_ledger = db["reputation_ledger"]
daily_caps = db["daily_caps"]
command_locks = db["command_locks"]
counters = db["counters"]

_db_write_lock = threading.RLock()


class MongoConn:
    def commit(self):
        return None

    def rollback(self):
        return None

    def backup(self, *_args, **_kwargs):
        raise NotImplementedError("SQLite backup is not available in Mongo mode")


conn = MongoConn()
cursor = None


@contextmanager
def write_txn():
    with _db_write_lock:
        yield


def next_id(name: str) -> int:
    doc = counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(doc["seq"])


def acquire_user_lock(user_id: int, lease_seconds: int = 12, wait_ms: int = 1200):
    lock_key = f"user:{int(user_id)}"
    token = uuid.uuid4().hex
    now = int(time.time())
    try:
        row = command_locks.find_one_and_update(
            {"lock_key": lock_key, "$or": [{"expires_at": {"$lte": now}}, {"holder": token}]},
            {
                "$set": {"holder": token, "expires_at": now + int(lease_seconds), "updated_at": now},
                "$setOnInsert": {"lock_key": lock_key, "created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        row = command_locks.find_one({"lock_key": lock_key}, {"_id": 0, "holder": 1, "expires_at": 1})
    if row and row.get("holder") == token and int(row.get("expires_at", 0) or 0) > now:
        return token
    return None


def release_user_lock(user_id: int, token: str):
    if not token:
        return
    command_locks.delete_one({"lock_key": f"user:{int(user_id)}", "holder": token})


def _ensure_indexes():
    citizens.create_index([("user_id", ASCENDING)], unique=True)
    businesses.create_index([("biz_id", ASCENDING)], unique=True)
    businesses.create_index([("owner_id", ASCENDING), ("is_bankrupt", ASCENDING)])
    businesses.create_index([("is_public", ASCENDING), ("is_bankrupt", ASCENDING), ("share_price", ASCENDING)])
    businesses.create_index([("is_bankrupt", ASCENDING), ("cash", ASCENDING)])
    businesses.create_index(
        [("owner_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"is_bankrupt": 0},
    )
    businesses.create_index(
        [("name_lc", ASCENDING)],
        unique=True,
        partialFilterExpression={"is_bankrupt": 0, "name_lc": {"$exists": True}},
    )
    market_goods.create_index([("good_id", ASCENDING)], unique=True)
    inventories.create_index([("user_id", ASCENDING), ("good_id", ASCENDING)], unique=True)
    market_listings.create_index([("listing_id", ASCENDING)], unique=True)
    market_listings.create_index([("good_id", ASCENDING), ("listed_at", ASCENDING)])
    loans.create_index([("loan_id", ASCENDING)], unique=True)
    portfolios.create_index([("user_id", ASCENDING), ("biz_id", ASCENDING)], unique=True)
    portfolios.create_index([("user_id", ASCENDING), ("shares", ASCENDING)])
    transactions.create_index([("tx_id", ASCENDING)], unique=True)
    transactions.create_index([("user_id", ASCENDING), ("timestamp", ASCENDING)])
    economy_state.create_index([("key", ASCENDING)], unique=True)
    active_events.create_index([("event_id", ASCENDING)], unique=True)
    government.create_index([("key", ASCENDING)], unique=True)
    admin_audit.create_index([("audit_id", ASCENDING)], unique=True)
    event_participants.create_index([("event_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
    quests_daily.create_index([("key", ASCENDING)], unique=True)
    quests_weekly.create_index([("key", ASCENDING)], unique=True)
    user_quests.create_index([("user_id", ASCENDING), ("quest_type", ASCENDING), ("quest_key", ASCENDING)], unique=True)
    achievements.create_index([("ach_key", ASCENDING)], unique=True)
    user_achievements.create_index([("user_id", ASCENDING), ("ach_key", ASCENDING)], unique=True)
    collections.create_index([("user_id", ASCENDING), ("collection_key", ASCENDING), ("item_key", ASCENDING)], unique=True)
    season_meta.create_index([("season_id", ASCENDING)], unique=True)
    season_meta.create_index([("status", ASCENDING)], unique=True, partialFilterExpression={"status": "active"})
    season_stats.create_index([("season_id", ASCENDING), ("user_id", ASCENDING)], unique=True)
    reminder_prefs.create_index([("user_id", ASCENDING)], unique=True)
    supply_chain_state.create_index([("category", ASCENDING)], unique=True)
    trust_edges.create_index([("src_user_id", ASCENDING), ("dst_user_id", ASCENDING)], unique=True)
    daily_caps.create_index([("cap_key", ASCENDING), ("user_id", ASCENDING), ("day_key", ASCENDING)], unique=True)
    command_locks.create_index([("lock_key", ASCENDING)], unique=True)
    command_locks.create_index([("expires_at", ASCENDING)])
    insurance_policies.create_index(
        [("holder_id", ASCENDING), ("policy_type", ASCENDING), ("status", ASCENDING)],
        unique=True,
        partialFilterExpression={"status": "active"},
    )


def _seed_defaults():
    defaults = {
        "inflation_rate": "0.02",
        "base_interest_rate": "0.05",
        "gdp": "0.0",
        "unemployment_rate": "0.0",
        "economic_phase": "stable",
        "total_money_supply": "0.0",
        "min_wage": "50.0",
        "last_simulation": str(int(time.time())),
        "consumer_confidence": "0.5",
        "business_confidence": "0.5",
        "policy_monetary_stance": "0.0",
        "policy_fiscal_stance": "0.0",
        "seasonality_strength": "0.15",
        "maintenance_mode": "0",
        "economy_frozen": "0",
        "events_enabled": "1",
        "global_money_multiplier": "1.0",
        "global_xp_multiplier": "1.0",
    }
    for key, value in defaults.items():
        economy_state.update_one({"key": key}, {"$setOnInsert": {"key": key, "value": value}}, upsert=True)

    gov_defaults = {"revenue": 0.0, "expenses": 0.0, "reserves": 50000.0}
    for key, value in gov_defaults.items():
        government.update_one({"key": key}, {"$setOnInsert": {"key": key, "value": float(value)}}, upsert=True)

    goods = [
        ("bread", "Bread", "food", 12.0, 0.04),
        ("meat", "Meat", "food", 45.0, 0.06),
        ("vegetables", "Vegetables", "food", 18.0, 0.05),
        ("coffee", "Coffee", "food", 25.0, 0.05),
        ("alcohol", "Alcohol", "food", 60.0, 0.07),
        ("steel", "Steel", "materials", 80.0, 0.06),
        ("wood", "Wood", "materials", 30.0, 0.05),
        ("plastic", "Plastic", "materials", 20.0, 0.04),
        ("concrete", "Concrete", "materials", 35.0, 0.04),
        ("copper", "Copper", "materials", 95.0, 0.07),
        ("chips", "Microchips", "tech", 200.0, 0.09),
        ("batteries", "Batteries", "tech", 55.0, 0.06),
        ("phones", "Smartphones", "tech", 450.0, 0.08),
        ("computers", "Computers", "tech", 900.0, 0.07),
        ("software", "Software", "tech", 300.0, 0.06),
        ("oil", "Oil (barrel)", "energy", 70.0, 0.10),
        ("coal", "Coal", "energy", 40.0, 0.07),
        ("solar", "Solar Panels", "energy", 250.0, 0.06),
        ("jewelry", "Jewelry", "luxury", 500.0, 0.10),
        ("art", "Fine Art", "luxury", 1200.0, 0.15),
    ]
    for good_id, name, category, price, volatility in goods:
        market_goods.update_one(
            {"good_id": good_id},
            {
                "$setOnInsert": {
                    "good_id": good_id,
                    "name": name,
                    "category": category,
                    "base_price": float(price),
                    "current_price": float(price),
                    "supply": 1000,
                    "demand": 500,
                    "volatility": float(volatility),
                }
            },
            upsert=True,
        )

    tiers = [
        ("homeless", 0.0, 0.0, 0.2, 999999),
        ("budget", 120.0, 10.0, 0.5, 2500),
        ("standard", 200.0, 20.0, 0.7, 2000),
        ("premium", 350.0, 35.0, 0.85, 1200),
        ("luxury", 600.0, 60.0, 0.95, 600),
    ]
    for tier, base_rent, upkeep, comfort, supply in tiers:
        housing_units.update_one(
            {"tier": tier},
            {"$setOnInsert": {"tier": tier, "base_rent": base_rent, "upkeep": upkeep, "comfort": comfort, "supply": supply}},
            upsert=True,
        )

    for category in ("food", "materials", "tech", "energy", "luxury"):
        supply_chain_state.update_one(
            {"category": category},
            {"$setOnInsert": {"category": category, "backlog": 0.0, "inventory": 0.0, "updated_at": int(time.time())}},
            upsert=True,
        )

    daily_seed = [
        ("daily_work_3", "Shift Worker", "Complete 3 work shifts today", "work_count", 3, 250.0, 25),
        ("daily_trade_5", "Trader", "Complete 5 market actions today", "trade_count", 5, 200.0, 20),
        ("daily_save_500", "Saver", "Increase bank balance by $500 today", "bank_gain", 500, 180.0, 15),
    ]
    weekly_seed = [
        ("weekly_work_15", "Workhorse", "Complete 15 shifts this week", "work_count", 15, 1200.0, 100),
        ("weekly_trade_20", "Market Maker", "Complete 20 trade actions this week", "trade_count", 20, 1000.0, 80),
    ]
    for key, title, description, target_type, target_value, reward_cash, reward_xp in daily_seed:
        quests_daily.update_one(
            {"key": key},
            {"$setOnInsert": {"key": key, "title": title, "description": description, "target_type": target_type, "target_value": target_value, "reward_cash": reward_cash, "reward_xp": reward_xp, "is_active": 1}},
            upsert=True,
        )
    for key, title, description, target_type, target_value, reward_cash, reward_xp in weekly_seed:
        quests_weekly.update_one(
            {"key": key},
            {"$setOnInsert": {"key": key, "title": title, "description": description, "target_type": target_type, "target_value": target_value, "reward_cash": reward_cash, "reward_xp": reward_xp, "is_active": 1}},
            upsert=True,
        )

    ach_seed = [
        ("ach_networth_10k", "Five Digits", "Reach $10,000 net worth", "net_worth", 10000, 500, "Bronze Saver"),
        ("ach_trade_100", "Floor Veteran", "Complete 100 trades", "trade_count", 100, 750, "Market Veteran"),
        ("ach_work_200", "Career Grinder", "Complete 200 shifts", "work_count", 200, 900, "Work Legend"),
    ]
    for ach_key, title, description, metric_key, target_value, reward_cash, reward_badge in ach_seed:
        achievements.update_one(
            {"ach_key": ach_key},
            {"$setOnInsert": {"ach_key": ach_key, "title": title, "description": description, "metric_key": metric_key, "target_value": target_value, "reward_cash": reward_cash, "reward_badge": reward_badge}},
            upsert=True,
        )

    active_season = season_meta.find_one({"status": "active"})
    if not active_season:
        now = int(time.time())
        sid = next_id("season_meta")
        try:
            season_meta.insert_one(
                {
                    "season_id": sid,
                    "name": f"Season {time.strftime('%Y-%m')}",
                    "starts_at": now,
                    "ends_at": now + 30 * 86400,
                    "status": "active",
                }
            )
        except DuplicateKeyError:
            pass


_ensure_indexes()
_seed_defaults()
