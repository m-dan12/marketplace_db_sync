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
