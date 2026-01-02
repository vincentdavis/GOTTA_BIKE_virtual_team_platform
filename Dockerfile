FROM ghcr.io/astral-sh/uv:python3.14-alpine

# Create and change to the app directory.
WORKDIR /app

# Copy local code to the container image.
COPY . .

# Install project dependencies.
RUN uv sync --frozen

# Create staticfiles directory (required by WhiteNoise at startup)
RUN mkdir -p /app/staticfiles

# Entrypoint script to handle collectstatic, migrations, and start the app.
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# Run the app using the script.
CMD ["/app/entrypoint.sh"]
