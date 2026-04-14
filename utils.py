import time
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
