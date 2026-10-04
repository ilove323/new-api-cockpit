ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home cockpit
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src/ ./src/
COPY sql/ ./sql/
RUN pip install --no-cache-dir --no-deps .
USER cockpit
EXPOSE 8000
ENTRYPOINT ["python", "-m", "new_api_cockpit.runtime"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "90", "--access-logfile", "-", "new_api_cockpit.app:app"]
