FROM node:22.19.0-bookworm-slim AS dependencies

WORKDIR /app
COPY package.json package-lock.json ./
COPY frontend/package.json frontend/package.json
RUN npm ci

FROM dependencies AS build

ENV NEXT_TELEMETRY_DISABLED=1
COPY frontend /app/frontend
RUN npm run build --workspace=frontend

FROM node:22.19.0-bookworm-slim AS runtime

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

WORKDIR /app
COPY --from=build /app/package.json /app/package-lock.json ./
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/frontend ./frontend

WORKDIR /app/frontend
EXPOSE 3002

CMD ["npm", "run", "start"]
