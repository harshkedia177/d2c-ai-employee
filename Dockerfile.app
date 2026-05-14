FROM python:3.12-slim
WORKDIR /app

# uv exports the locked deps; pip installs them into the system python.
# No venv — the container itself is the isolation boundary.
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --format requirements-txt > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY . /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

CMD ["uvicorn", "packages.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
