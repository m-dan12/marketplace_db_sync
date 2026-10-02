"""Non-secret runtime settings."""

# One wide window of orders/sales is pulled every night and upserted by
# natural key; 7d/30d/"previous 30d" slices are `WHERE date >= ...` reads
# from the DB, not separate pulls (see TASK.md §5).
SYNC_WINDOW_DAYS = 65

# Advertising statistics settle with a delay, so the last days are re-pulled
# (and upserted) every night.
AD_WINDOW_DAYS = 7

# Selsup movement history is re-read for the last few days each night; the
# upsert key makes overlapping windows harmless and covers missed runs.
SELSUP_MOVEMENTS_WINDOW_DAYS = 3

# WB supplies are re-read when updated within this many days; Ozon supply
# orders: the newest N pages (100 each) of all states, besides every order
# that is still in an active state.
SUPPLIES_WINDOW_DAYS = 21
OZON_SUPPLY_RECENT_PAGES = 2

# WB sales funnel: the last N days (ending yesterday) are re-pulled each night
# because buyouts and cancellations settle with a delay.
FUNNEL_WINDOW_DAYS = 3

# WB promotions: regular promotions whose end falls within this many days
# back (or later) get their participating products read each night.
PROMOTIONS_ITEMS_LOOKBACK_DAYS = 3
PROMOTIONS_CALENDAR_START = "2026-01-01"
PROMOTIONS_CALENDAR_AHEAD_DAYS = 120

# Google Sheets kept by hand (shared with the service account that reads Drive).
# The ids are not secrets: access is granted to the account, not by the link.
PLANNING_SPREADSHEET_ID = "1cdx99nrElv08rygrDnWrFmnJviJXjnPxLkWZqSkhvJI"
PRODUCTION_SPREADSHEET_ID = "1pBYf7pG2Yj-hL1Q5XYytbXkBP2xT2Lvv0TFrC-kLGxI"
SUPPLY_PLAN_SPREADSHEET_ID = "1Bq5Jz6WNtoz38-E9JWjdRkE-s6EqVaq0dppxKpSFU70"
