#!/bin/sh

# Exit immediately if a command exits with a non-zero status.
set -e

# Install production dependencies
#echo "Installing production dependencies..."
#uv sync --frozen

# Run Django migrations.
echo "Running migrations..."
uv run manage.py migrate

# Seed the canonical Zwift dataset + vELO2 weights on first deploy only. Both are
# --if-empty (no-op once populated) and non-fatal (|| true): seeding must never keep
# the web server from starting. After first seed, the scheduler keeps the dataset
# fresh and admins can re-run either from the /routes/ page.
echo "Seeding Zwift route/segment dataset (if empty)..."
uv run manage.py sync_zwift_data --if-empty || echo "warning: zwift_data seed failed (retry via scheduler / Check for updates)"

echo "Seeding vELO2 route factor weights (if empty)..."
uv run manage.py import_velo_weights --if-empty || echo "warning: vELO seed failed (retry via Load vELO weights)"

# Create superuser if not exists
#echo "Creating superuser if not exists..."
#uv run manage.py ensuresuperuser

# Start the background task worker
echo "Starting background task worker..."
uv run manage.py db_worker &

# Start the scheduler (replaces external cron service)
echo "Starting scheduler..."
uv run manage.py scheduler &

# Start the server with Granian.
# Bound workers x blocking-threads: each blocking thread can hold one persistent DB
# connection, and Granian auto-sizes blocking-threads from the (shared, large) host
# CPU count if left unset — which exhausted Postgres ("too many clients already").
# Peak web DB connections = WEB_WORKERS x WEB_BLOCKING_THREADS. Tune via env if needed.
echo "Starting server with Granian..."
uv run granian gotta_bike_platform.wsgi:application --interface wsgi --host 0.0.0.0 --port "${PORT:-8000}" \
  --workers "${WEB_WORKERS:-2}" --blocking-threads "${WEB_BLOCKING_THREADS:-2}"