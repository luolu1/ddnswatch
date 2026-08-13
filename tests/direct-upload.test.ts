import { describe, expect, it } from "vitest"
import { createEmbeddedAssets } from "../src/embedded-assets"
import { createWorker } from "../src/index"

const dashboard = createEmbeddedAssets({
  html: "<!doctype html><title>DDNS Watch</title>",
  javascript: 'document.documentElement.dataset.loaded = "true"',
  css: "body { color: green; }",
})
const directWorker = createWorker(dashboard)
const env = { MONITOR_CONFIG_JSON: JSON.stringify({ targets: [] }) } as const
const scheduledController: ScheduledController = {
  cron: "* * * * *",
  scheduledTime: 0,
  noRetry: () => undefined,
}

describe("direct-upload Worker", () => {
  it("serves embedded dashboard routes and rejects unknown paths", async () => {
    // Given
    const requests = [
      new Request("https://watch.test/"),
      new Request("https://watch.test/app.js"),
      new Request("https://watch.test/style.css"),
    ] as const

    // When
    const [html, javascript, css] = await Promise.all([
      directWorker.fetch(requests[0], env),
      directWorker.fetch(requests[1], env),
      directWorker.fetch(requests[2], env),
    ])
    const missing = await directWorker.fetch(new Request("https://watch.test/missing"), env)

    // Then
    expect(await html.text()).toContain("DDNS Watch")
    expect(html.headers.get("content-type")).toBe("text/html; charset=utf-8")
    expect(javascript.headers.get("content-type")).toBe("text/javascript; charset=utf-8")
    expect(css.headers.get("content-type")).toBe("text/css; charset=utf-8")
    expect(missing.status).toBe(404)
  })

  it("serves health and stateless status without an ASSETS binding", async () => {
    // Given
    const healthRequest = new Request("https://watch.test/health")
    const statusRequest = new Request("https://watch.test/api/status")

    // When
    const health = await directWorker.fetch(healthRequest, env)
    const status = await directWorker.fetch(statusRequest, env)

    // Then
    expect(await health.json()).toEqual({ status: "ok" })
    expect(await status.json()).toMatchObject({ refresh_seconds: 60, targets: [] })
  })

  it("retains scheduled Telegram notification semantics without an ASSETS binding", async () => {
    // Given
    const calls: string[] = []
    const originalFetch = globalThis.fetch
    globalThis.fetch = async (input) => {
      calls.push(String(input))
      return String(input).includes("telegram.org")
        ? Response.json({ ok: true })
        : Response.json({ Answer: [] })
    }
    const scheduledEnv = {
      MONITOR_CONFIG_JSON: JSON.stringify({
        targets: [{ host: "unresolved.test", port: 443 }],
        telegram: { enabled: true },
      }),
      TELEGRAM_BOT_TOKEN: "test-token",
      TELEGRAM_CHAT_ID: "test-chat",
    } as const

    // When
    try {
      await directWorker.scheduled(scheduledController, scheduledEnv)
    } finally {
      globalThis.fetch = originalFetch
    }

    // Then
    expect(calls.some((url) => url.includes("bottest-token/sendMessage"))).toBe(true)
  })
})
