import time
import json
import math
from db import cursor, conn

EDUCATION_LEVELS = ["none", "highschool", "college", "masters", "phd"]
EDUCATION_COSTS = {"highschool": 500, "college": 2500, "masters": 8000, "phd": 20000}
EDUCATION_SALARY_BONUS = {"none": 1.0, "highschool": 1.10, "college": 1.25, "masters": 1.40, "phd": 1.55}

TAX_BRACKETS = [
    (200,  0.05),
    (500,  0.12),
    (1000, 0.22),
    (float("inf"), 0.30),
]

JOB_LEVELS = [
    (0,    "Entry",    1.00),
    (500,  "Junior",   1.25),
    (1500, "Mid",      1.50),
    (3000, "Senior",   1.75),
    (6000, "Expert",   2.00),
]


def ensure_citizen(discord_id: int):
    cursor.execute("SELECT user_id FROM citizens WHERE user_id = ?", (discord_id,))
    if cursor.fetchone() is None:
        cursor.execute(
            "INSERT INTO citizens(user_id, registered_at) VALUES (?, ?)",
            (discord_id, int(time.time()))
        )
        conn.commit()


def get_citizen(discord_id: int):
    cursor.execute("SELECT * FROM citizens WHERE user_id = ?", (discord_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))


def log_tx(user_id: int, tx_type: str, amount: float, description: str):
    cursor.execute(
        "INSERT INTO transactions(user_id, tx_type, amount, description, timestamp) VALUES (?, ?, ?, ?, ?)",
        (user_id, tx_type, amount, description, int(time.time()))
    )
    conn.commit()


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
    cursor.execute(
        "SELECT trust_score FROM trust_edges WHERE src_user_id = ? AND dst_user_id = ?",
        (src_user_id, dst_user_id)
    )
    row = cursor.fetchone()
    return float(row[0]) if row else 0.0


def update_trust(src_user_id: int, dst_user_id: int, delta: float, reason: str = None):
    now = int(time.time())
    cursor.execute(
        "SELECT trust_score, interactions FROM trust_edges WHERE src_user_id = ? AND dst_user_id = ?",
        (src_user_id, dst_user_id)
    )
    row = cursor.fetchone()
    if row:
        score, interactions = float(row[0]), int(row[1])
    else:
        score, interactions = 0.0, 0
    new_score = clamp(score + float(delta), -1.0, 1.0)
    cursor.execute(
        "INSERT OR REPLACE INTO trust_edges(src_user_id, dst_user_id, trust_score, interactions, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (src_user_id, dst_user_id, new_score, interactions + 1, now)
    )
    if reason:
        cursor.execute(
            "INSERT INTO reputation_ledger(entity_type, entity_id, delta, reason, source_type, source_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("citizen", dst_user_id, float(delta), reason, "trust", str(src_user_id), now)
        )
    conn.commit()


def add_reputation(entity_type: str, entity_id: int, delta: float, reason: str = None, source_type: str = None, source_id: str = None):
    now = int(time.time())
    cursor.execute(
        "INSERT INTO reputation_ledger(entity_type, entity_id, delta, reason, source_type, source_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (entity_type, int(entity_id), float(delta), reason, source_type, source_id, now)
    )
    conn.commit()


def get_housing_tiers():
    cursor.execute("SELECT tier, base_rent, upkeep, comfort, supply FROM housing_units ORDER BY base_rent ASC")
    return cursor.fetchall()


def housing_cost_for_tier(tier: str) -> tuple[float, float, float]:
    cursor.execute("SELECT base_rent, upkeep, comfort FROM housing_units WHERE tier = ? LIMIT 1", (tier,))
    row = cursor.fetchone()
    if not row:
        return 200.0, 20.0, 0.7
    return float(row[0]), float(row[1]), float(row[2])


def record_employment_event(user_id: int, event_type: str, job_id: str = None, details: str = None):
    cursor.execute(
        "INSERT INTO employment_history(user_id, event_type, job_id, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (int(user_id), event_type, job_id, details, int(time.time()))
    )
    conn.commit()


def record_offense(offender_id: int, offense_type: str, severity: int, fine_amount: float, jail_seconds: int, detected_prob: float):
    cursor.execute(
        "INSERT INTO offenses(offender_id, offense_type, severity, fine_amount, jail_seconds, detected_prob_snapshot, committed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(offender_id), offense_type, int(severity), float(fine_amount), int(jail_seconds), float(detected_prob), int(time.time()))
    )
    conn.commit()


def snapshot_macro(**kwargs):
    ts = int(time.time())
    cols = [
        "ts", "inflation", "base_interest", "unemployment", "gdp_proxy", "money_supply", "velocity_proxy",
        "avg_credit_score", "gov_reserves", "active_loans", "active_businesses", "bankrupt_businesses", "defaults_last_7d"
    ]
    values = [ts] + [kwargs.get(k) for k in cols[1:]]
    cursor.execute(
        f"INSERT OR REPLACE INTO macro_snapshots({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
        values
    )
    conn.commit()


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
    for i, (req_xp, lvl_title, mult) in enumerate(JOB_LEVELS):
        if req_xp > xp:
            next_xp = req_xp
            break
    return level_num, title, multiplier, next_xp


def get_eco_state(key: str):
    cursor.execute("SELECT value FROM economy_state WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else None


def set_eco_state(key: str, value):
    cursor.execute(
        "INSERT OR REPLACE INTO economy_state(key, value) VALUES (?, ?)",
        (key, str(value))
    )
    conn.commit()


def get_gov(key: str) -> float:
    cursor.execute("SELECT value FROM government WHERE key = ?", (key,))
    row = cursor.fetchone()
    return float(row[0]) if row else 0.0


def set_gov(key: str, value: float):
    cursor.execute(
        "INSERT OR REPLACE INTO government(key, value) VALUES (?, ?)",
        (key, value)
    )
    conn.commit()


def add_gov_revenue(amount: float):
    current = get_gov("revenue")
    set_gov("revenue", current + amount)
    reserves = get_gov("reserves")
    set_gov("reserves", reserves + amount)


def deduct_gov_expense(amount: float):
    current = get_gov("expenses")
    set_gov("expenses", current + amount)
    reserves = get_gov("reserves")
    set_gov("reserves", max(0.0, reserves - amount))


def credit_score_label(score: int) -> str:
    if score >= 800:
        return "Exceptional"
    elif score >= 740:
        return "Very Good"
    elif score >= 670:
        return "Good"
    elif score >= 580:
        return "Fair"
    else:
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
    cursor.execute("SELECT user_id FROM citizens")
    return [r[0] for r in cursor.fetchall()]


def get_active_season():
    cursor.execute(
        "SELECT season_id, name, starts_at, ends_at FROM season_meta WHERE status = 'active' ORDER BY season_id DESC LIMIT 1"
    )
    return cursor.fetchone()


def update_season_stat(user_id: int, metric: str, amount: float):
    season = get_active_season()
    if not season:
        return
    season_id = int(season[0])
    cursor.execute(
        "INSERT OR IGNORE INTO season_stats(season_id, user_id, updated_at) VALUES (?, ?, ?)",
        (season_id, int(user_id), int(time.time())),
    )
    allowed = {"net_worth", "trade_volume", "work_shifts", "quests_completed"}
    if metric not in allowed:
        return
    if metric in {"work_shifts", "quests_completed"}:
        cursor.execute(
            f"UPDATE season_stats SET {metric} = {metric} + ?, updated_at = ? WHERE season_id = ? AND user_id = ?",
            (int(amount), int(time.time()), season_id, int(user_id)),
        )
    else:
        cursor.execute(
            f"UPDATE season_stats SET {metric} = {metric} + ?, updated_at = ? WHERE season_id = ? AND user_id = ?",
            (float(amount), int(time.time()), season_id, int(user_id)),
        )
    conn.commit()


def increment_quest_progress(user_id: int, target_type: str, delta: float = 1.0):
    cursor.execute(
        "UPDATE user_quests SET progress = MIN(target, progress + ?) "
        "WHERE user_id = ? AND target_type = ? AND claimed = 0 AND resets_at > ?",
        (float(delta), int(user_id), target_type, int(time.time())),
    )
    conn.commit()


def ensure_user_achievements(user_id: int):
    cursor.execute("SELECT ach_key, target_value FROM achievements")
    for ach_key, target_value in cursor.fetchall():
        cursor.execute(
            "INSERT OR IGNORE INTO user_achievements(user_id, ach_key, progress) VALUES (?, ?, 0.0)",
            (int(user_id), ach_key),
        )
    conn.commit()


def increment_achievement_progress(user_id: int, metric_key: str, delta: float = 1.0):
    ensure_user_achievements(user_id)
    cursor.execute(
        "SELECT ua.ach_key, ua.progress, ua.unlocked, a.target_value "
        "FROM user_achievements ua JOIN achievements a ON ua.ach_key = a.ach_key "
        "WHERE ua.user_id = ? AND a.metric_key = ?",
        (int(user_id), metric_key),
    )
    rows = cursor.fetchall()
    now = int(time.time())
    for ach_key, progress, unlocked, target in rows:
        new_progress = min(float(target), float(progress) + float(delta))
        if unlocked:
            cursor.execute(
                "UPDATE user_achievements SET progress = ? WHERE user_id = ? AND ach_key = ?",
                (new_progress, int(user_id), ach_key),
            )
        else:
            will_unlock = 1 if new_progress >= float(target) else 0
            unlock_ts = now if will_unlock else 0
            cursor.execute(
                "UPDATE user_achievements SET progress = ?, unlocked = ?, unlocked_at = CASE WHEN ? = 1 THEN ? ELSE unlocked_at END "
                "WHERE user_id = ? AND ach_key = ?",
                (new_progress, will_unlock, will_unlock, unlock_ts, int(user_id), ach_key),
            )
    conn.commit()


def set_reminder_pref(user_id: int, dm_enabled: int = None, daily_ready: int = None, work_ready: int = None, quest_ready: int = None):
    cursor.execute("INSERT OR IGNORE INTO reminder_prefs(user_id, updated_at) VALUES (?, ?)", (int(user_id), int(time.time())))
    updates = []
    params = []
    if dm_enabled is not None:
        updates.append("dm_enabled = ?")
        params.append(int(bool(dm_enabled)))
    if daily_ready is not None:
        updates.append("daily_ready = ?")
        params.append(int(bool(daily_ready)))
    if work_ready is not None:
        updates.append("work_ready = ?")
        params.append(int(bool(work_ready)))
    if quest_ready is not None:
        updates.append("quest_ready = ?")
        params.append(int(bool(quest_ready)))
    if updates:
        updates.append("updated_at = ?")
        params.append(int(time.time()))
        params.append(int(user_id))
        cursor.execute(f"UPDATE reminder_prefs SET {', '.join(updates)} WHERE user_id = ?", params)
        conn.commit()


def get_reminder_pref(user_id: int):
    cursor.execute(
        "SELECT dm_enabled, daily_ready, work_ready, quest_ready FROM reminder_prefs WHERE user_id = ?",
        (int(user_id),),
    )
    row = cursor.fetchone()
    if not row:
        return {"dm_enabled": 0, "daily_ready": 1, "work_ready": 1, "quest_ready": 1}
    return {"dm_enabled": int(row[0]), "daily_ready": int(row[1]), "work_ready": int(row[2]), "quest_ready": int(row[3])}


def record_retention_metric(metric_name: str, metric_value: float, day_key: str = None):
    if day_key is None:
        day_key = time.strftime("%Y-%m-%d", time.gmtime())
    cursor.execute(
        "INSERT INTO retention_metrics(day_key, metric_name, metric_value, created_at) VALUES (?, ?, ?, ?)",
        (day_key, metric_name, float(metric_value), int(time.time())),
    )
    conn.commit()
