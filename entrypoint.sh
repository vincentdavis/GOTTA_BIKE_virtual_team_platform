#!/bin/sh

# Exit immediately if a command exits with a non-zero status.
set -e

# Install production dependencies
#echo "Installing production dependencies..."
#uv sync --frozen

# Run Django migrations.
echo "Running migrations..."
uv run manage.py migrate

# Create superuser if not exists
#echo "Creating superuser if not exists..."
#uv run manage.py ensuresuperuser

# Start the background task worker
echo "Starting background task worker..."
uv run manage.py db_worker &

# Start the server with Granian.
echo "Starting server with Granian..."
uv run granian gotta_bike_platform.wsgi:application --interface wsgi --host 0.0.0.0 --port "${PORT:-8000}" --workers 4