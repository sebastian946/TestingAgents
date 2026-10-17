# Production image of the frontend: the static build served by Caddy, which also proxies the
# API (app/deploy/Caddyfile). Replaces the plain `caddy` image in docker-compose.prod.yml.
FROM node:24-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /src/dist /srv
