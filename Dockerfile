ARG PYTHON_IMAGE=python:3.12-slim
ARG NODE_IMAGE=node:22-slim
FROM --platform=$BUILDPLATFORM ${NODE_IMAGE} AS vsix-builder
WORKDIR /build/vsix
COPY vsix/package.json vsix/package-lock.json ./
RUN npm ci --ignore-scripts
COPY vsix/src/ ./src/
COPY vsix/scripts/ ./scripts/
COPY vsix/media/ ./media/
COPY vsix/tsconfig.json vsix/.vscodeignore vsix/README.md vsix/CHANGELOG.md vsix/LICENSE vsix/THIRD_PARTY_NOTICES.txt ./
RUN npm run package && node scripts/export-download.cjs /out

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends antiword fonts-wqy-zenhei && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home cockpit
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src/ ./src/
COPY --from=vsix-builder /out/ ./src/new_api_cockpit/bundled_clients/
COPY sql/ ./sql/
RUN pip install --no-cache-dir --no-deps .
USER cockpit
EXPOSE 8000
ENTRYPOINT ["python", "-m", "new_api_cockpit.runtime"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "90", "--access-logfile", "-", "new_api_cockpit.app:app"]
