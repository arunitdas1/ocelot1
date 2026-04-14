# Economy Bot — Full Macroeconomic Simulation

A deeply realistic, persistent, Discord economy bot that simulates a living, breathing national economy.

## Tech Stack
- **Language:** Python 3.11
- **Library:** discord.py 2.x
- **Database:** SQLite (WAL mode, `economy.db`)
- **Prefix:** `!`

## Architecture — Cogs

| Cog | File | Purpose |
|-----|------|---------|
| Profile | `cogs/profile.py` | Player profiles, balances, payments, history |
| Jobs | `cogs/jobs.py` | 22 jobs across 6 tiers, XP, education, skill |
| Banking | `cogs/banking.py` | Deposits, withdrawals, loans, credit scores |
| Market | `cogs/market.py` | Buy/sell goods, P2P listings, dynamic prices |
| Business | `cogs/business.py` | Found, manage, grow companies |
| Stocks | `cogs/stocks.py` | IPOs, stock trading, portfolios, capital gains |
| Government | `cogs/government.py` | Tax policy, stimulus, min wage (admin) |
| Indicators | `cogs/indicators.py` | GDP, inflation, unemployment, trends |
| Events | `cogs/events_cog.py` | View active random economic events |
| Economy Engine | `cogs/economy_engine.py` | Background simulation (market, events, expenses) |

## Database Tables
- `citizens` — player stats, job, education, housing, credit
- `businesses` — company data, revenue, employees, stock
- `market_goods` — 20 goods with dynamic prices, supply/demand
- `inventories` — player item inventories
- `market_listings` — P2P market listings
- `loans` — active/paid/defaulted loans
- `portfolios` — stock holdings per player
- `transactions` — full audit log
- `economy_state` — global KV store (inflation, GDP phase, etc.)
- `active_events` — live random economic events
- `government` — budget, reserves, expenses

## Economy Engine (Background Tasks)
- **Every 5 min:** Market price simulation (supply/demand, inflation drift)
- **Every 30 min:** Random event trigger (12 event types)
- **Every hour:** Loan interest accrual, living expenses, welfare payments, business cycles

## Key Commands
- `!profile` `!balance` `!pay` `!daily` `!history`
- `!jobs` `!apply` `!work` `!career` `!educate` `!train` `!resign`
- `!deposit` `!withdraw` `!loan` `!repay` `!loans` `!credit`
- `!market` `!buy` `!sell` `!inventory` `!listings` `!delist`
- `!startbiz` `!mybiz` `!hire` `!fire` `!bizwithdraw`
- `!stocks` `!ipo` `!invest` `!divest` `!portfolio`
- `!economy` `!inflation` `!gdp` `!unemployment` `!richlist`
- `!events` `!govbudget` `!taxrate`
- Admin only: `!stimulus` `!setrate` `!setphase` `!setminwage` `!printmoney`
