"""Non-secret runtime settings."""

# One wide window of orders/sales is pulled every night and upserted by
# natural key; 7d/30d/"previous 30d" slices are `WHERE date >= ...` reads
# from the DB, not separate pulls (see TASK.md §5).
SYNC_WINDOW_DAYS = 65
