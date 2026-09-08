FROM node:22.19.0-bookworm-slim

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY sandbox.js server.js tasks.js ./

ENV NODE_ENV=production
USER 65532:65532
EXPOSE 8080
CMD ["node", "server.js"]
