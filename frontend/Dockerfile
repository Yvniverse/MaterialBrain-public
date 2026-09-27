FROM node:22-alpine AS builder
WORKDIR /app
ENV CI=true
RUN corepack enable
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,id=materialbrain-pnpm-store,target=/pnpm/store \
    pnpm install --frozen-lockfile --store-dir=/pnpm/store
ARG VITE_BUILD_SHA=unknown
ARG VITE_RELEASE_TAG=
ARG VITE_BUILD_TIME_UTC=
ENV VITE_BUILD_SHA=$VITE_BUILD_SHA \
    VITE_RELEASE_TAG=$VITE_RELEASE_TAG \
    VITE_BUILD_TIME_UTC=$VITE_BUILD_TIME_UTC
COPY . .
RUN pnpm run build \
    && printf '{"frontend_build_sha":"%s","release_tag":"%s","build_time_utc":"%s"}\n' \
      "$VITE_BUILD_SHA" "$VITE_RELEASE_TAG" "$VITE_BUILD_TIME_UTC" \
      > /app/dist/build-info.json

FROM nginx:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY docker.nginx.conf /etc/nginx/conf.d/default.conf
HEALTHCHECK --interval=15s --timeout=5s --retries=5 CMD wget -qO- http://127.0.0.1/healthz || exit 1
