# Akasha UI image: the production bundle behind nginx, which also proxies
# /api (live WebSocket included) to the API service, so browser and API
# share one origin (no CORS). See docs/deployment.md.

FROM node:24.14.0-alpine AS build
WORKDIR /app
# Dependencies first: the layer survives source-only changes.
COPY package.json package-lock.json .npmrc ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29.1-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
# The UI itself, not the API: the API service has its own healthcheck.
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
