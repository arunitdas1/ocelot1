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
