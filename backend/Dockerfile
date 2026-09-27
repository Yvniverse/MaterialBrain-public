FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-mcp.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir --no-deps -r requirements-mcp.txt
ARG MATERIALBRAIN_BUILD_SHA=unknown
ARG MATERIALBRAIN_RELEASE_TAG=
ARG MATERIALBRAIN_BUILD_TIME_UTC=
ENV MATERIALBRAIN_BUILD_SHA=$MATERIALBRAIN_BUILD_SHA \
    MATERIALBRAIN_RELEASE_TAG=$MATERIALBRAIN_RELEASE_TAG \
    MATERIALBRAIN_BUILD_TIME_UTC=$MATERIALBRAIN_BUILD_TIME_UTC
COPY . .
RUN mkdir -p /storage/attachments /storage/evidence
CMD ["sh", "-c", "alembic upgrade head && python scripts/seed_default_data.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
