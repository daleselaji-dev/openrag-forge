FROM node:24-slim AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install
COPY web ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .
COPY --from=web-build /web/dist ./web/dist
COPY recipes ./recipes
COPY packs ./packs
EXPOSE 8000
CMD ["uvicorn", "openrag_forge.app:app", "--host", "0.0.0.0", "--port", "8000"]
