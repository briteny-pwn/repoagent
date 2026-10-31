FROM node:24-alpine AS build
WORKDIR /web
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build
FROM caddy:2-alpine
COPY --from=build /web/dist /srv
