# Build context is the REPO ROOT, not backend/, because the image needs the
# starter-pack data (corpus/, data/, schema/, dev/) as well as the app.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_ROOT=/srv/starter-pack

WORKDIR /srv/app

# uv from PyPI rather than a COPY --from of the ghcr image: one less registry
# to depend on in CI, and pyproject.toml stays the single source of truth.
RUN pip install --no-cache-dir uv==0.7.12

# Dependencies first so edits to app code don't bust the layer cache.
COPY backend/pyproject.toml ./pyproject.toml
RUN uv pip install --system --no-cache -r pyproject.toml

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./alembic.ini
COPY backend/scripts ./scripts

# The corpus and sample addresses the modules read at runtime.
COPY corpus /srv/starter-pack/corpus
COPY data /srv/starter-pack/data
COPY schema /srv/starter-pack/schema
COPY dev /srv/starter-pack/dev

RUN useradd --create-home --uid 10001 app && chown -R app:app /srv
USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status==200 else 1)"

# The corpus ships inside this image, so the database should match the image
# rather than whatever a one-off seed left behind months ago. `seed_documents`
# upserts from the manifest and never touches `document_note`, so re-seeding on
# every boot keeps a re-captured document in step with the repository without
# discarding what extraction recorded about it.
CMD ["sh", "-c", "alembic upgrade head && python scripts/seed.py && exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers 1"]
