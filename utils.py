import json
import math
import time
from pymongo import ReturnDocument

from db import (
    achievements,
    citizens,
    daily_caps,
    economy_state,
    employment_history,
    government,
    housing_units,
    macro_snapshots,
    offenses,
    reminder_prefs,
    reputation_ledger,
    retention_metrics,
    season_meta,
    season_stats,
    transactions,
    trust_edges,
    user_achievements,
    user_quests,
)

EDUCATION_LEVELS = ["none", "highschool", "college", "masters", "phd"]
EDUCATION_COSTS = {"highschool": 500, "college": 2500, "masters": 8000, "phd": 20000}
EDUCATION_SALARY_BONUS = {"none": 1.0, "highschool": 1.10, "college": 1.25, "masters": 1.40, "phd": 1.55}

TAX_BRACKETS = [
    (200, 0.05),
    (500, 0.12),
    (1000, 0.22),
    (float("inf"), 0.30),
]

JOB_LEVELS = [
    (0, "Entry", 1.00),
    (500, "Junior", 1.25),
    (1500, "Mid", 1.50),
    (3000, "Senior", 1.75),
    (6000, "Expert", 2.00),
]

_ECO_CACHE: dict[str, tuple[float, str]] = {}
_ECO_CACHE_TTL_SEC = 0.5
_HOUSING_CACHE: tuple[float, dict[str, tuple[float, float, float, int]]] = (0.0, {})
_HOUSING_CACHE_TTL_SEC = 300.0
_ACTIVE_SEASON_CACHE: tuple[float, tuple | None] = (0.0, None)
_ACTIVE_SEASON_TTL_SEC = 30.0


def _citizen_defaults(discord_id: int):
    return {
        "user_id": int(discord_id),
        "cash": 1000.0,
        "bank": 0.0,
        "credit_score": 650,
        "skill_level": 1,
        "education": "none",
        "happiness": 75.0,
        "job_id": None,
        "job_xp": 0,
        "last_work": 0,
        "last_daily": 0,
        "debt": 0.0,
        "housing": "renting",
        "last_expense": 0,
        "registered_at": int(time.time()),
        "is_jailed": 0,
        "lifestyle_tier": "standard",
        "debt_stress_score": 0.0,
        "criminal_record_points": 0,
        "wanted_level": 0,
        "last_release_at": 0,
        "daily_streak": 0,
        "last_streak_claim": 0,
        "streak_protect_tokens": 0,
    }


def ensure_citizen(discord_id: int):
    citizens.update_one({"user_id": int(discord_id)}, {"$setOnInsert": _citizen_defaults(discord_id)}, upsert=True)


def get_citizen(discord_id: int):
    row = citizens.find_one({"user_id": int(discord_id)}, {"_id": 0})
    return row or None


def log_tx(user_id: int, tx_type: str, amount: float, description: str):
    from db import next_id

    amt = safe_float(amount, 0.0)
    if not math.isfinite(amt):
        return
    transactions.insert_one(
        {
            "tx_id": next_id("transactions"),
            "user_id": int(user_id),
            "tx_type": tx_type,
            "amount": amt,
            "description": str(description or ""),
            "timestamp": int(time.time()),
        }
    )


def fmt(amount: float) -> str:
    return f"${amount:,.2f}"


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def safe_float(value, default: float = 0.0) -> float:
    try:
        x = float(value)
        if not math.isfinite(x):
            return default
        return x
    except Exception:
        return default


def safe_json_loads(s: str, default):
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


def get_trust(src_user_id: int, dst_user_id: int) -> float:
    row = trust_edges.find_one({"src_user_id": int(src_user_id), "dst_user_id": int(dst_user_id)}, {"_id": 0, "trust_score": 1})
    return float(row["trust_score"]) if row else 0.0


def update_trust(src_user_id: int, dst_user_id: int, delta: float, reason: str = None):
    now = int(time.time())
    trust_edges.update_one(
        {"src_user_id": int(src_user_id), "dst_user_id": int(dst_user_id)},
        [
            {
                "$set": {
                    "src_user_id": int(src_user_id),
                    "dst_user_id": int(dst_user_id),
                    "trust_score": {
                        "$max": [
                            -1.0,
                            {
                                "$min": [
                                    1.0,
                                    {"$add": [{"$ifNull": ["$trust_score", 0.0]}, float(delta)]},
                                ]
                            },
                        ]
                    },
                    "interactions": {"$add": [{"$ifNull": ["$interactions", 0]}, 1]},
                    "updated_at": now,
                }
            }
        ],
        upsert=True,
    )
    if reason:
        reputation_ledger.insert_one(
            {
                "entity_type": "citizen",
                "entity_id": int(dst_user_id),
                "delta": float(delta),
                "reason": reason,
                "source_type": "trust",
                "source_id": str(src_user_id),
                "created_at": now,
            }
        )


def add_reputation(entity_type: str, entity_id: int, delta: float, reason: str = None, source_type: str = None, source_id: str = None):
    reputation_ledger.insert_one(
        {
            "entity_type": entity_type,
            "entity_id": int(entity_id),
            "delta": float(delta),
            "reason": reason,
            "source_type": source_type,
            "source_id": source_id,
            "created_at": int(time.time()),
        }
    )


def get_housing_tiers():
    global _HOUSING_CACHE
    now = time.monotonic()
    cached_ts, cached_map = _HOUSING_CACHE
    if cached_map and now - cached_ts < _HOUSING_CACHE_TTL_SEC:
        rows = [(tier, vals[0], vals[1], vals[2], vals[3]) for tier, vals in cached_map.items()]
        rows.sort(key=lambda x: x[1])
        return rows
    rows = list(housing_units.find({}, {"_id": 0, "tier": 1, "base_rent": 1, "upkeep": 1, "comfort": 1, "supply": 1}).sort("base_rent", 1))
    cache_map = {
        r["tier"]: (float(r["base_rent"]), float(r["upkeep"]), float(r["comfort"]), int(r["supply"]))
        for r in rows
    }
    _HOUSING_CACHE = (now, cache_map)
    return [(r["tier"], r["base_rent"], r["upkeep"], r["comfort"], r["supply"]) for r in rows]


def housing_cost_for_tier(tier: str) -> tuple[float, float, float]:
    global _HOUSING_CACHE
    now = time.monotonic()
    cached_ts, cached_map = _HOUSING_CACHE
    if not cached_map or now - cached_ts >= _HOUSING_CACHE_TTL_SEC:
        get_housing_tiers()
        cached_ts, cached_map = _HOUSING_CACHE
    vals = cached_map.get(tier)
    if not vals:
        return 200.0, 20.0, 0.7
    return vals[0], vals[1], vals[2]


def record_employment_event(user_id: int, event_type: str, job_id: str = None, details: str = None):
    employment_history.insert_one(
        {"user_id": int(user_id), "event_type": event_type, "job_id": job_id, "details": details, "created_at": int(time.time())}
    )


def record_offense(offender_id: int, offense_type: str, severity: int, fine_amount: float, jail_seconds: int, detected_prob: float):
    offenses.insert_one(
        {
            "offender_id": int(offender_id),
            "offense_type": offense_type,
            "severity": int(severity),
            "fine_amount": float(fine_amount),
            "jail_seconds": int(jail_seconds),
            "detected_prob_snapshot": float(detected_prob),
            "committed_at": int(time.time()),
        }
    )


def snapshot_macro(**kwargs):
    ts = int(time.time())
    doc = {
        "ts": ts,
        "inflation": kwargs.get("inflation"),
        "base_interest": kwargs.get("base_interest"),
        "unemployment": kwargs.get("unemployment"),
        "gdp_proxy": kwargs.get("gdp_proxy"),
        "money_supply": kwargs.get("money_supply"),
        "velocity_proxy": kwargs.get("velocity_proxy"),
        "avg_credit_score": kwargs.get("avg_credit_score"),
        "gov_reserves": kwargs.get("gov_reserves"),
        "active_loans": kwargs.get("active_loans"),
        "active_businesses": kwargs.get("active_businesses"),
        "bankrupt_businesses": kwargs.get("bankrupt_businesses"),
        "defaults_last_7d": kwargs.get("defaults_last_7d"),
    }
    macro_snapshots.update_one({"ts": ts}, {"$set": doc}, upsert=True)


def calculate_income_tax(gross: float) -> float:
    tax = 0.0
    prev = 0.0
    for bracket_max, rate in TAX_BRACKETS:
        if gross <= prev:
            break
        taxable = min(gross, bracket_max) - prev
        tax += taxable * rate
        prev = bracket_max
    return round(tax, 2)


def get_job_level(xp: int):
    level_num, title, multiplier = JOB_LEVELS[0]
    for i, (req_xp, lvl_title, mult) in enumerate(JOB_LEVELS):
        if xp >= req_xp:
            level_num = i + 1
            title = lvl_title
            multiplier = mult
    next_xp = None
    for req_xp, _, _ in JOB_LEVELS:
        if req_xp > xp:
            next_xp = req_xp
            break
    return level_num, title, multiplier, next_xp


def get_eco_state(key: str):
    now = time.monotonic()
    cached = _ECO_CACHE.get(key)
    if cached and now - cached[0] < _ECO_CACHE_TTL_SEC:
        return cached[1]
    row = economy_state.find_one({"key": key}, {"_id": 0, "value": 1})
    value = row["value"] if row else None
    if value is not None:
        _ECO_CACHE[key] = (now, value)
    return value


def get_eco_states(keys: list[str]) -> dict[str, str | None]:
    now = time.monotonic()
    result: dict[str, str | None] = {}
    missing: list[str] = []
    for key in keys:
        cached = _ECO_CACHE.get(key)
        if cached and now - cached[0] < _ECO_CACHE_TTL_SEC:
            result[key] = cached[1]
        else:
            missing.append(key)
    if missing:
        rows = list(economy_state.find({"key": {"$in": missing}}, {"_id": 0, "key": 1, "value": 1}))
        for row in rows:
            k = row["key"]
            v = row.get("value")
            result[k] = v
            _ECO_CACHE[k] = (now, v)
        for key in missing:
            result.setdefault(key, None)
    return result


def set_eco_state(key: str, value):
    sval = str(value)
    economy_state.update_one({"key": key}, {"$set": {"key": key, "value": sval}}, upsert=True)
    _ECO_CACHE[key] = (time.monotonic(), sval)


def get_gov(key: str) -> float:
    row = government.find_one({"key": key}, {"_id": 0, "value": 1})
    return float(row["value"]) if row else 0.0


def set_gov(key: str, value: float):
    amount = safe_float(value, float("nan"))
    if not math.isfinite(amount):
        return
    government.update_one({"key": key}, {"$set": {"key": key, "value": amount}}, upsert=True)


def add_gov_revenue(amount: float):
    amount = safe_float(amount, 0.0)
    if amount <= 0:
        return
    # Group correlated government updates under the write lock to reduce partial-update windows.
    from db import write_txn

    with write_txn():
        government.update_one({"key": "revenue"}, {"$setOnInsert": {"key": "revenue", "value": 0.0}, "$inc": {"value": amount}}, upsert=True)
        government.update_one({"key": "reserves"}, {"$setOnInsert": {"key": "reserves", "value": 0.0}, "$inc": {"value": amount}}, upsert=True)


def deduct_gov_expense(amount: float):
    amount = safe_float(amount, 0.0)
    if amount <= 0:
        return
    from db import write_txn

    with write_txn():
        government.update_one({"key": "expenses"}, {"$setOnInsert": {"key": "expenses", "value": 0.0}, "$inc": {"value": amount}}, upsert=True)
        government.update_one({"key": "reserves"}, {"$setOnInsert": {"key": "reserves", "value": 0.0}, "$inc": {"value": -amount}}, upsert=True)
        government.update_one({"key": "reserves", "value": {"$lt": 0.0}}, {"$set": {"value": 0.0}})


def credit_score_label(score: int) -> str:
    if score >= 800:
        return "Exceptional"
    if score >= 740:
        return "Very Good"
    if score >= 670:
        return "Good"
    if score >= 580:
        return "Fair"
    return "Poor"


def get_loan_interest_rate(credit_score: int) -> float:
    base = float(get_eco_state("base_interest_rate") or 0.05)
    if credit_score >= 800:
        modifier = -0.02
    elif credit_score >= 740:
        modifier = -0.01
    elif credit_score >= 670:
        modifier = 0.0
    elif credit_score >= 580:
        modifier = 0.03
    else:
        modifier = 0.07
    return round(base + modifier, 4)


def housing_expense(housing: str) -> float:
    return {"homeless": 0.0, "renting": 150.0, "owned": 75.0}.get(housing, 150.0)


def get_all_citizens():
    return [doc["user_id"] for doc in citizens.find({}, {"_id": 0, "user_id": 1})]


def get_active_season():
    global _ACTIVE_SEASON_CACHE
    now = time.monotonic()
    cached_ts, cached_value = _ACTIVE_SEASON_CACHE
    if now - cached_ts < _ACTIVE_SEASON_TTL_SEC:
        return cached_value
    row = season_meta.find_one({"status": "active"}, {"_id": 0, "season_id": 1, "name": 1, "starts_at": 1, "ends_at": 1}, sort=[("season_id", -1)])
    if not row:
        _ACTIVE_SEASON_CACHE = (now, None)
        return None
    value = (row["season_id"], row["name"], row["starts_at"], row["ends_at"])
    _ACTIVE_SEASON_CACHE = (now, value)
    return value


def update_season_stat(user_id: int, metric: str, amount: float):
    season = get_active_season()
    if not season:
        return
    allowed = {"net_worth", "trade_volume", "work_shifts", "quests_completed"}
    if metric not in allowed:
        return
    season_id = int(season[0])
    delta = int(amount) if metric in {"work_shifts", "quests_completed"} else float(amount)
    season_stats.update_one(
        {"season_id": season_id, "user_id": int(user_id)},
        {
            "$setOnInsert": {"season_id": season_id, "user_id": int(user_id), "net_worth": 0.0, "trade_volume": 0.0, "work_shifts": 0, "quests_completed": 0},
            "$inc": {metric: delta},
            "$set": {"updated_at": int(time.time())},
        },
        upsert=True,
    )


def increment_quest_progress(user_id: int, target_type: str, delta: float = 1.0):
    now = int(time.time())
    user_quests.update_many(
        {"user_id": int(user_id), "target_type": target_type, "claimed": 0, "resets_at": {"$gt": now}},
        [
            {
                "$set": {
                    "progress": {
                        "$min": [
                            {"$ifNull": ["$target", 1.0]},
                            {"$add": [{"$ifNull": ["$progress", 0.0]}, float(delta)]},
                        ]
                    }
                }
            }
        ],
    )


def ensure_user_achievements(user_id: int):
    rows = list(achievements.find({}, {"_id": 0, "ach_key": 1}))
    for row in rows:
        user_achievements.update_one(
            {"user_id": int(user_id), "ach_key": row["ach_key"]},
            {"$setOnInsert": {"user_id": int(user_id), "ach_key": row["ach_key"], "progress": 0.0, "unlocked": 0, "claimed": 0, "unlocked_at": 0}},
            upsert=True,
        )


def increment_achievement_progress(user_id: int, metric_key: str, delta: float = 1.0):
    ensure_user_achievements(user_id)
    docs = list(
        achievements.aggregate(
            [
                {"$match": {"metric_key": metric_key}},
                {"$lookup": {"from": "user_achievements", "localField": "ach_key", "foreignField": "ach_key", "as": "ua"}},
                {"$unwind": "$ua"},
                {"$match": {"ua.user_id": int(user_id)}},
                {"$project": {"ach_key": 1, "target_value": 1, "progress": "$ua.progress", "unlocked": "$ua.unlocked"}},
            ]
        )
    )
    now = int(time.time())
    for doc in docs:
        ach_key = doc["ach_key"]
        target = float(doc["target_value"])
        user_achievements.update_one(
            {"user_id": int(user_id), "ach_key": ach_key},
            [
                {
                    "$set": {
                        "progress": {
                            "$min": [
                                target,
                                {"$add": [{"$ifNull": ["$progress", 0.0]}, float(delta)]},
                            ]
                        },
                        "unlocked": {
                            "$max": [
                                {"$ifNull": ["$unlocked", 0]},
                                {
                                    "$cond": [
                                        {
                                            "$gte": [
                                                {
                                                    "$min": [
                                                        target,
                                                        {"$add": [{"$ifNull": ["$progress", 0.0]}, float(delta)]},
                                                    ]
                                                },
                                                target,
                                            ]
                                        },
                                        1,
                                        0,
                                    ]
                                },
                            ]
                        },
                        "unlocked_at": {
                            "$cond": [
                                {
                                    "$and": [
                                        {"$eq": [{"$ifNull": ["$unlocked", 0]}, 0]},
                                        {
                                            "$gte": [
                                                {
                                                    "$min": [
                                                        target,
                                                        {"$add": [{"$ifNull": ["$progress", 0.0]}, float(delta)]},
                                                    ]
                                                },
                                                target,
                                            ]
                                        },
                                    ]
                                },
                                now,
                                {"$ifNull": ["$unlocked_at", 0]},
                            ]
                        },
                    }
                }
            ],
        )


def set_reminder_pref(user_id: int, dm_enabled: int = None, daily_ready: int = None, work_ready: int = None, quest_ready: int = None):
    updates = {"updated_at": int(time.time())}
    if dm_enabled is not None:
        updates["dm_enabled"] = int(bool(dm_enabled))
    if daily_ready is not None:
        updates["daily_ready"] = int(bool(daily_ready))
    if work_ready is not None:
        updates["work_ready"] = int(bool(work_ready))
    if quest_ready is not None:
        updates["quest_ready"] = int(bool(quest_ready))
    reminder_prefs.update_one(
        {"user_id": int(user_id)},
        {
            "$setOnInsert": {"user_id": int(user_id), "dm_enabled": 0, "daily_ready": 1, "work_ready": 1, "quest_ready": 1},
            "$set": updates,
        },
        upsert=True,
    )


def get_reminder_pref(user_id: int):
    row = reminder_prefs.find_one({"user_id": int(user_id)}, {"_id": 0, "dm_enabled": 1, "daily_ready": 1, "work_ready": 1, "quest_ready": 1})
    if not row:
        return {"dm_enabled": 0, "daily_ready": 1, "work_ready": 1, "quest_ready": 1}
    return {
        "dm_enabled": int(row.get("dm_enabled", 0)),
        "daily_ready": int(row.get("daily_ready", 1)),
        "work_ready": int(row.get("work_ready", 1)),
        "quest_ready": int(row.get("quest_ready", 1)),
    }


def record_retention_metric(metric_name: str, metric_value: float, day_key: str = None):
    if day_key is None:
        day_key = time.strftime("%Y-%m-%d", time.gmtime())
    retention_metrics.insert_one(
        {"day_key": day_key, "metric_name": metric_name, "metric_value": float(metric_value), "created_at": int(time.time())}
    )


def reserve_daily_cap(user_id: int, cap_key: str, limit: int, now_ts: int = None) -> bool:
    now = int(now_ts or time.time())
    day_key = time.strftime("%Y-%m-%d", time.gmtime(now))
    doc = daily_caps.find_one_and_update(
        {"cap_key": cap_key, "user_id": int(user_id), "day_key": day_key},
        {
            "$inc": {"count": 1},
            "$setOnInsert": {"cap_key": cap_key, "user_id": int(user_id), "day_key": day_key, "created_at": now},
            "$set": {"updated_at": now},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    count = int((doc or {}).get("count", 0))
    if count <= int(limit):
        return True
    daily_caps.update_one(
        {"cap_key": cap_key, "user_id": int(user_id), "day_key": day_key, "count": {"$gt": 0}},
        {"$inc": {"count": -1}, "$set": {"updated_at": now}},
    )
    return False


def release_daily_cap(user_id: int, cap_key: str, now_ts: int = None):
    now = int(now_ts or time.time())
    day_key = time.strftime("%Y-%m-%d", time.gmtime(now))
    daily_caps.update_one(
        {"cap_key": cap_key, "user_id": int(user_id), "day_key": day_key, "count": {"$gt": 0}},
        {"$inc": {"count": -1}, "$set": {"updated_at": now}},
    )
