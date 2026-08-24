# Mailigence backend — FastAPI + SQLAlchemy 2.0 (async) + psycopg 3.
#
# psycopg[binary] ships prebuilt wheels for linux/amd64 slim images, so no
# build toolchain (MSVC / gcc / libpq headers) is needed — the image stays
# small and the build stays fast. Table creation + migrations run on startup
# via app.main.lifespan (init_db).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY run.py ./
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

EXPOSE 8000

CMD ["./entrypoint.sh"]
