ARG PYTHON_IMAGE=python@sha256:79082fdf411b897d64983893f44e8e1908e21cade2fc5a794d4f46de76c85275
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/services/api:/app/services/worker:/app/packages/domain-python
WORKDIR /app
COPY infrastructure/local/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r /tmp/requirements.txt
COPY services /app/services
COPY packages /app/packages
COPY database /app/database
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin kajh
USER 10001:10001
CMD ["python", "-m", "uvicorn", "kajh_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
