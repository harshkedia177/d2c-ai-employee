FROM python:3.12-slim
WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn faker

# Build context is the project root; copy the package preserving its dotted path.
COPY mock_saas/ /app/mock_saas/

# Pre-generate seed JSON at image build time so the container starts instantly
# with data — the recruiter never has to push the gigabytes of demo JSON.
RUN python -m mock_saas.seed.generate --merchants=1 --orders-per-merchant=2000

ENV PYTHONUNBUFFERED=1
EXPOSE 9000

HEALTHCHECK --interval=3s --timeout=2s --retries=10 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9000/openapi.json', timeout=1).status == 200 else 1)" || exit 1

# Entrypoint regenerates seed at runtime if it's missing (e.g. an anonymous
# volume gets mounted over /app/mock_saas/seed/data). Belt-and-suspenders.
ENTRYPOINT ["/app/mock_saas/entrypoint.sh"]
CMD ["uvicorn", "mock_saas.server:app", "--host", "0.0.0.0", "--port", "9000"]
