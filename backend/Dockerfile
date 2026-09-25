FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

# Runs as root so pytest/alembic can write into the bind-mounted source on any host.
FROM base AS dev
COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY . .
CMD ["flask", "--app", "app", "run", "--debug", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS prod
COPY . .
RUN useradd --system --no-create-home app
USER app
EXPOSE 8000
CMD ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"]
