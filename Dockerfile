FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY tennis_overview /app/tennis_overview

RUN pip install --no-cache-dir . \
    && mkdir -p /ms-playwright \
    && python -m playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

RUN useradd --create-home appuser
USER appuser

ENV TENNIS_CONFIG=/config/clubs.toml
ENV TENNIS_DATA_DIR=/data
ENV PORT=8080

VOLUME ["/data"]
EXPOSE 8080

CMD ["tennis-overview", "serve", "--host", "0.0.0.0", "--port", "8080", "--initial-sync"]
