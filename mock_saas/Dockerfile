FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn faker
COPY . /app
CMD ["uvicorn", "mock_saas.server:app", "--host", "0.0.0.0", "--port", "9000"]
