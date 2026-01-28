FROM ghcr.io/astral-sh/uv:python3.14-alpine

# Install Node.js for Tailwind CSS build
RUN apk add --no-cache nodejs npm

# Create and change to the app directory.
WORKDIR /app

# Copy local code to the container image.
COPY . .

# Install project dependencies.
RUN uv sync --frozen --compile-bytecode

# Install Tailwind CSS dependencies
RUN uv run python manage.py tailwind install

# Build Tailwind CSS
RUN uv run python manage.py tailwind build

# Collect static files (WhiteNoise serves from this directory)
RUN uv run python manage.py collectstatic --noinput

# Entrypoint script to handle migrations and start the app.
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Run the app using the script.
CMD ["/app/entrypoint.sh"]
