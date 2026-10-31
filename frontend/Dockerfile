FROM node:20-alpine AS build
WORKDIR /app
ARG NPM_REGISTRY=https://registry.npmjs.org/
COPY package.json ./
RUN npm config set registry $NPM_REGISTRY && npm install --no-audit --no-fund
COPY . .
RUN npm run build
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
