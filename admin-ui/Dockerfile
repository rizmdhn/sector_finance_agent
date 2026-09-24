# Multi-stage: build the static bundle with Node, serve it with nginx — the built
# image never contains node_modules or source, only the compiled dist/ output plus
# nginx itself.

FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
# nginx's official image runs every *.template in this directory through envsubst
# at container start (docker-entrypoint.d/20-envsubst-on-templates.sh), writing the
# result to /etc/nginx/conf.d/ — only tokens matching a real environment variable
# name get replaced, so nginx's own $uri/$host etc. pass through untouched. See
# nginx.conf.template's own header comment for why this matters here
# ($IDX_GATEWAY_KEY never reaches the browser).
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

EXPOSE 80
