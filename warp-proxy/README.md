# Automatic WARP WireProxy gateway

`docker compose up -d --build` builds wgcf `v2.2.29` and WireProxy `v1.1.2`
from pinned Go module versions. On first start, the gateway runs
`wgcf register --accept-tos` and `wgcf generate`, rewrites the profile to an
IPv4-only route, and starts WireProxy with HTTP on `25345`, SOCKS5 on `25344`,
and readiness on `9080`. The generated account and profiles persist in the
Compose named volume `warp-state`.

The gateway uses the unofficial wgcf client and Cloudflare's consumer WARP
service. Registration depends on the upstream service and may be unavailable,
rate-limited, or change without notice. No fscarmen credentials or startup
remote installer is used.

Verify the IPv4 egress:

```bash
docker compose exec ddnswatch python -c \
  "import httpx; print(httpx.get('https://www.cloudflare.com/cdn-cgi/trace', timeout=10, trust_env=True).text)"
```

The output must contain `warp=on`.

This verifies an HTTPS request through the same environment proxy contract as
DDNSWatch. It does not proxy or test libc DNS; DDNSWatch hostname resolution
continues to use the container resolver.

Reset the generated account and register a new one:

```bash
docker compose down -v
docker compose up -d --build
```
