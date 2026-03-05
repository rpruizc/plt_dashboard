FROM python:3.12-slim

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml .python-version ./

# Install dependencies (no dev, no editable)
RUN uv sync --no-dev --no-install-project

# Copy application code
COPY . .

# Create data directory and seed with local DB if present
RUN mkdir -p /data
COPY territory.db /data/territory.db

# Expose port
EXPOSE 8080

# Run with gunicorn for production
RUN uv pip install gunicorn

CMD ["uv", "run", "gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "2", "--timeout", "120", "app:app", "--preload"]
