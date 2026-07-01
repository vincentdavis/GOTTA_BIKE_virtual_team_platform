#!/bin/sh

# Exit immediately if a command exits with a non-zero status.
set -e

# Install production dependencies
#echo "Installing production dependencies..."
#uv sync --frozen

# Run Django migrations.
echo "Running migrations..."
uv run manage.py migrate

# Seed vELO2 route factor weights on first deploy only. --if-empty makes this a
# no-op once any route has weights, so later restarts won't clobber manual edits
# made via the route form.
echo "Seeding vELO2 route factor weights (if empty)..."
uv run manage.py import_velo_weights --if-empty

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
echo "Starting server with Granian..."
uv run granian gotta_bike_platform.wsgi:application --interface wsgi --host 0.0.0.0 --port "${PORT:-8000}" --workers 4