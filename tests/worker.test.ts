import { describe, expect, it } from "vitest"
import * as z from "zod"
import { parseConfig } from "../src/config"
import { resolveIpv4 } from "../src/dns"
import worker from "../src/index"
import {
  aggregateTcpPingResults,
  extractAntifloodCookie,
  extractTcpPingTask,
} from "../src/tcp-ping"

const configJson = JSON.stringify({ targets: [] })
const env = {
  MONITOR_CONFIG_JSON: configJson,
  ASSETS: { fetch: async () => new Response("asset") },
} as const
const scheduledController: ScheduledController = {
  cron: "* * * * *",
  scheduledTime: 0,
  noRetry: () => undefined,
}
const statusSchema = z
  .object({
    refresh_seconds: z.literal(60),
    checked_at: z.string(),
    targets: z.array(
      z
        .object({
          name: z.string().nullable(),
          host: z.string(),
          port: z.number(),
          resolved_ip: z.string().nullable(),
          status: z.enum(["normal", "blocked", "unknown"]),
          reason: z.string(),
          checked_at: z.string(),
        })
        .strict(),
    ),
  })
  .strict()

describe("configuration and tcp.ping.pe semantics", () => {
  it("parses the JSON boundary and defaults tcp settings", () => {
    const config = parseConfig(configJson)
    const configured = parseConfig(
      JSON.stringify({ targets: [{ host: "example.com", port: 443 }] }),
    )
    expect(configured.targets[0]?.host).toBe("example.com")
    expect(config.checkIntervalSeconds).toBe(60)
    expect(config.tcpPing.minCnProbes).toBe(3)
    expect(config.tcpPing.maxPolls).toBe(15)
    expect(() => parseConfig(JSON.stringify({ check_interval_seconds: 25 }))).toThrow()
    expect(() =>
      parseConfig(
        JSON.stringify({
          targets: [
            { host: "one.example", port: 443 },
            { host: "two.example", port: 443 },
            { host: "three.example", port: 443 },
          ],
        }),
      ),
    ).toThrow()
    expect(() => parseConfig(JSON.stringify({ tcp_ping: { max_polls: 16 } }))).toThrow()
    expect(parseConfig(JSON.stringify({ tcp_ping: {} })).tcpPing).not.toHaveProperty("enabled")
    expect(() => parseConfig(JSON.stringify({ tcp_ping: { enabled: false } }))).toThrow()
    expect(() =>
      parseConfig(JSON.stringify({ telegram: { enabled: true, bot_token: "secret" } })),
    ).toThrow()
  })

  it("extracts browser token and antiflood cookie", () => {
    expect(
      extractTcpPingTask("taskStartQuery='ip:443'; taskStartToken=\"tok\"; interval_s=2"),
    ).toEqual({ query: "ip:443", token: "tok", intervalSeconds: 2 })
    expect(extractAntifloodCookie('document.cookie="foo=bar; antiflood=abc123; path=/"')).toBe(
      "abc123",
    )
  })

  it("classifies mainland probe results", () => {
    const result = aggregateTcpPingResults(
      [
        { node_id: "a", location: "China", result: 1 },
        { node_id: "b", location: "中国", result: 1 },
        { node_id: "c", location: "Beijing", result: 1 },
      ],
      3,
      0.2,
    )
    expect(result.status).toBe("blocked")
  })
})

describe("Worker routes", () => {
  it("serves health and stateless status", async () => {
    const health = await worker.fetch(new Request("https://watch.test/health"), env)
    expect(health.status).toBe(200)
    const status = await worker.fetch(new Request("https://watch.test/api/status"), env)
    const body: unknown = await status.json()
    expect(body).toMatchObject({ refresh_seconds: 60, targets: [] })
    expect(body).not.toHaveProperty("latest")
  })

  it("returns live status with only stateless target fields", async () => {
    const originalFetch = globalThis.fetch
    globalThis.fetch = async () => Response.json({ Answer: [{ type: 1, data: "198.51.100.10" }] })
    try {
      const response = await worker.fetch(new Request("https://watch.test/api/status"), {
        ...env,
        MONITOR_CONFIG_JSON: JSON.stringify({ targets: [{ host: "example.com", port: 443 }] }),
      })
      const body: unknown = await response.json()
      expect(statusSchema.parse(body).targets).toHaveLength(1)
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it("rejects non-GET known and API routes and delegates unknown assets", async () => {
    expect(
      (await worker.fetch(new Request("https://watch.test/health", { method: "POST" }), env))
        .status,
    ).toBe(405)
    expect(
      (await worker.fetch(new Request("https://watch.test/api/status", { method: "POST" }), env))
        .status,
    ).toBe(405)
    const missing = await worker.fetch(new Request("https://watch.test/api/missing"), env)
    expect(missing.status).toBe(404)
    expect(await missing.json()).toEqual({ error: "not found" })
    expect(
      (await worker.fetch(new Request("https://watch.test/api/missing", { method: "POST" }), env))
        .status,
    ).toBe(404)
  })

  it("delegates non-api routes to assets", async () => {
    const response = await worker.fetch(new Request("https://watch.test/app"), env)
    expect(await response.text()).toBe("asset")
  })
})

describe("scheduled checks", () => {
  it("checks targets sequentially and notifies each blocked or unknown result", async () => {
    const calls: string[] = []
    const telegramBodies: string[] = []
    const scheduledEnv = {
      MONITOR_CONFIG_JSON: JSON.stringify({
        targets: [
          { host: "198.51.100.1", port: 443 },
          { host: "198.51.100.2", port: 443 },
        ],
        tcp_ping: { min_cn_probes: 1, max_polls: 1, poll_interval_seconds: 0 },
        telegram: { enabled: true },
      }),
      ASSETS: env.ASSETS,
      TELEGRAM_BOT_TOKEN: "token",
      TELEGRAM_CHAT_ID: "42",
    } as const
    const originalFetch = globalThis.fetch
    globalThis.fetch = async (input, init) => {
      const request = new Request(input, init)
      const url = request.url
      calls.push(url)
      if (url.includes("telegram.org")) {
        telegramBodies.push(await request.text())
        return Response.json({ ok: true })
      }
      if (url.includes("ajax_stopTask")) return Response.json({})
      if (url.includes("ajax_getPingResults"))
        return Response.json({
          state: { outstandingNodeCount: 0 },
          data: [
            { node_id: url.includes("stream-1") ? "one" : "two", location: "China", result: 1 },
          ],
        })
      if (url.includes("ajax_startTask")) {
        const number = calls.filter((call) => call.includes("ajax_startTask")).length
        return Response.json({ ok: true, data: { stream_id: `stream-${number}` } })
      }
      return new Response("taskStartQuery='ip:443'; taskStartToken='dynamic'; interval_s=0", {
        status: 200,
      })
    }
    try {
      await worker.scheduled(scheduledController, scheduledEnv)
    } finally {
      globalThis.fetch = originalFetch
    }
    expect(calls.filter((url) => url.includes("telegram.org"))).toHaveLength(2)
    expect(
      calls.every((url) => !url.includes("telegram.org") || url.includes("bottoken/sendMessage")),
    ).toBe(true)
    expect(telegramBodies.every((body) => body.includes('"chat_id":"42"'))).toBe(true)
    expect(calls.findIndex((url) => url.includes("198.51.100.2"))).toBeGreaterThan(
      calls.findIndex((url) => url.includes("ajax_stopTask") && url.includes("stream-1")),
    )
  })

  it("rejects after checks when Telegram fails", async () => {
    const scheduledEnv = {
      MONITOR_CONFIG_JSON: JSON.stringify({
        targets: [{ host: "bad host", port: 443 }],
        telegram: { enabled: true },
      }),
      ASSETS: env.ASSETS,
      TELEGRAM_BOT_TOKEN: "token",
      TELEGRAM_CHAT_ID: "42",
    } as const
    const originalFetch = globalThis.fetch
    globalThis.fetch = async (input) =>
      String(input).includes("dns-query")
        ? Response.json({ Answer: [] })
        : new Response("failure", { status: 500 })
    try {
      await expect(worker.scheduled(scheduledController, scheduledEnv)).rejects.toThrow(
        "Telegram notifications failed",
      )
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it("requires both Telegram bindings when enabled", async () => {
    const enabled = {
      MONITOR_CONFIG_JSON: JSON.stringify({ telegram: { enabled: true } }),
      ASSETS: env.ASSETS,
    }
    await expect(worker.scheduled(scheduledController, enabled)).rejects.toThrow(
      "TELEGRAM_BOT_TOKEN",
    )
    await expect(
      worker.fetch(new Request("https://watch.test/api/status"), enabled),
    ).rejects.toThrow("TELEGRAM_BOT_TOKEN")
  })

  it("does not notify during HTTP status checks", async () => {
    const originalFetch = globalThis.fetch
    const calls: string[] = []
    globalThis.fetch = async (input) => {
      calls.push(String(input))
      return Response.json({ Answer: [] })
    }
    try {
      await worker.fetch(new Request("https://watch.test/api/status"), {
        ...env,
        MONITOR_CONFIG_JSON: JSON.stringify({
          targets: [{ host: "example.com", port: 443 }],
          telegram: { enabled: true },
        }),
        TELEGRAM_BOT_TOKEN: "token",
        TELEGRAM_CHAT_ID: "42",
      })
    } finally {
      globalThis.fetch = originalFetch
    }
    expect(calls.some((url) => url.includes("telegram.org"))).toBe(false)
  })
})

describe("Cloudflare DoH", () => {
  it("returns IPv4 literals without fetching", async () => {
    let called = false
    const result = await resolveIpv4("198.51.100.5", async () => {
      called = true
      return Response.json({})
    })
    expect(result).toBe("198.51.100.5")
    expect(called).toBe(false)
  })
  it("returns first A answer and handles no answer and HTTP errors", async () => {
    const fetcher = async () =>
      Response.json({
        Answer: [
          { type: 28, data: "::1" },
          { type: 1, data: "203.0.113.9" },
        ],
      })
    expect(await resolveIpv4("example.com", fetcher)).toBe("203.0.113.9")
    expect(
      await resolveIpv4("empty.test", async () => Response.json({ Answer: [] })),
    ).toBeUndefined()
    await expect(
      resolveIpv4("broken.test", async () => new Response("x", { status: 503 })),
    ).rejects.toThrow("DoH HTTP 503")
  })
})
