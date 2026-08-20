FROM golang:1.26.0-alpine3.23 AS build

ARG TARGETARCH
ENV CGO_ENABLED=0 \
    GOOS=linux

RUN apk add --no-cache git
RUN git clone --branch v2.2.29 --depth 1 https://github.com/ViRb3/wgcf.git /src/wgcf \
    && test "$(git -C /src/wgcf rev-parse HEAD)" = "7f74511fa8cd1187df4b1d5351ebda3dcab82825"
RUN git clone --branch v1.1.2 --depth 1 https://github.com/windtf/wireproxy.git /src/wireproxy \
    && test "$(git -C /src/wireproxy rev-parse HEAD)" = "3792cd42b2ffb9653fb4beb3fe9c66d1512f9ce0"
RUN build_arch="${TARGETARCH:-$(go env GOARCH)}" \
    && test "$build_arch" = "amd64" -o "$build_arch" = "arm64" \
    && cd /src/wgcf \
    && GOARCH="$build_arch" go build -trimpath -ldflags="-s -w" -o /out/wgcf .
RUN build_arch="${TARGETARCH:-$(go env GOARCH)}" \
    && cd /src/wireproxy \
    && GOARCH="$build_arch" go build -trimpath -ldflags="-s -w" -o /out/wireproxy ./cmd/wireproxy

FROM alpine:3.23.3

RUN apk add --no-cache ca-certificates wget
COPY --from=build /out/wgcf /usr/local/bin/wgcf
COPY --from=build /out/wireproxy /usr/local/bin/wireproxy
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY endpoint.sh failover.sh profile.sh supervisor.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh /usr/local/bin/endpoint.sh /usr/local/bin/failover.sh /usr/local/bin/profile.sh /usr/local/bin/supervisor.sh \
    && mkdir -p /var/lib/warp

VOLUME ["/var/lib/warp"]
EXPOSE 25344 25345 9080
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
