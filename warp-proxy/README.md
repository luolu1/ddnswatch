# Automatic WARP WireProxy gateway

`docker compose up -d --build` builds wgcf `v2.2.29` and WireProxy `v1.1.2`
from pinned Go module versions. On first start, the gateway runs
`wgcf register --accept-tos` and `wgcf generate`, rewrites the profile to an
IPv4-only route, and starts WireProxy with HTTP on `25345`, SOCKS5 on `25344`,
and readiness on `9080`. Mixed DNS entries are replaced with the IPv4 resolver
`1.1.1.1`, so WireProxy cannot select an unreachable IPv6 resolver. The generated account and profiles
persist in the Compose named volume `warp-state`.

The entrypoint supervises WireProxy and probes its real `/readyz` endpoint. A
single failed probe does nothing. After the grace period and six consecutive
failures, the supervisor stops WireProxy, archives the stale account and
profiles under `rotations/`, registers a fresh account, regenerates the IPv4
profile, and starts WireProxy again. Successful rotations increment the
credential-free `rotation-generation` counter. Registration retries use capped
exponential backoff, and rotation attempts are persisted and suppressed during
the cooldown to prevent registration storms. Ordinary container restarts reuse
the existing account and profile.

Configuration variables (seconds unless noted):

| Variable | Default | Purpose |
| --- | ---: | --- |
| `WARP_READINESS_GRACE_PERIOD` | `15` | Delay before runtime readiness probes |
| `WARP_READINESS_INTERVAL` | `10` | Delay between probes |
| `WARP_READINESS_FAILURES` | `6` | Consecutive failures required for rotation |
| `WARP_ROTATION_COOLDOWN` | `300` | Minimum delay between rotation attempts |
| `WARP_REGISTRATION_BACKOFF` | `5` | Initial registration retry delay |
| `WARP_REGISTRATION_MAX_ATTEMPTS` | `5` | Bounded registration attempts per rotation |

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
