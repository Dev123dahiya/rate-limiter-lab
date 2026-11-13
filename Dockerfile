FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml example_app.py ./
COPY ratelimit ./ratelimit
RUN pip install --no-cache-dir ".[demo]"
RUN useradd --create-home appuser
USER appuser
EXPOSE 8000
CMD ["uvicorn", "example_app:app", "--host", "0.0.0.0", "--port", "8000"]
