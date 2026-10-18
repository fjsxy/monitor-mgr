# syntax=docker/dockerfile:1
FROM node:26.4.0-bookworm-slim@sha256:ec82d089a8ae2cf02628da7b34ea57dc357b24db724d557fe2d240e6beb659c1 AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build
FROM golang:1.27.1-bookworm@sha256:8d48e12ec56735e9358640898b9d9b9fcca110612ed8a5567438c0a1baa24e66 AS go
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY cmd/ cmd/
COPY internal/ internal/
COPY kitex_gen/ kitex_gen/
ARG SOURCE_SHA=development
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w -X main.buildSource=${SOURCE_SHA}" -o /monitor-mgr ./cmd/monitor-mgr
FROM gcr.io/distroless/static-debian12:nonroot@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab
ARG SOURCE_SHA=development
LABEL org.opencontainers.image.source="https://github.com/fjsxy/monitor-mgr" org.opencontainers.image.revision="${SOURCE_SHA}"
COPY --from=go /monitor-mgr /monitor-mgr
COPY --from=web /src/web/dist /web
USER 10001:10001
EXPOSE 8080
ENTRYPOINT ["/monitor-mgr"]
CMD ["-data-dir","/data","-http",":8080","-rpc","127.0.0.1:8888","-static","/web"]
