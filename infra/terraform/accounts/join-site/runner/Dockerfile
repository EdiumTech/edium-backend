FROM node:22.19.0-bookworm-slim

WORKDIR /app
COPY runner/package.json runner/package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY runner/sandbox.js runner/server.js runner/tasks.js runner/dispatcher.js ./
COPY functions/app/contest_catalog.json ./contest_catalog.json

ENV NODE_ENV=production
ENV CONTEST_CATALOG_PATH=/app/contest_catalog.json
USER 65532:65532
EXPOSE 8080
CMD ["node", "server.js"]
