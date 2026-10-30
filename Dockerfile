# RingLens live demo: one container serving the FastAPI backend and the built React console.
# The databases are external (MongoDB Atlas free + Neo4j AuraDB Free); see render.yaml.

FROM node:22-slim AS console
WORKDIR /app/frontend
RUN corepack enable && corepack prepare pnpm@10.28.0 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.11-slim
WORKDIR /app/backend
COPY backend/pyproject.toml ./
COPY backend/src ./src
# Editable install keeps api.py at /app/backend/src, so it finds /app/frontend/dist.
RUN pip install --no-cache-dir -e ".[aura]"
COPY --from=console /app/frontend/dist /app/frontend/dist
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh", "-c", "uvicorn ringlens.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
