import * as z from "zod"
import { parseConfig } from "./config"
import { checkAll } from "./monitor"
import { notifyTelegram } from "./telegram"

const envSchema = z.object({
  MONITOR_CONFIG_JSON: z.string().optional(),
  TELEGRAM_BOT_TOKEN: z.string().optional(),
  TELEGRAM_CHAT_ID: z.string().optional(),
  ASSETS: z.custom<Readonly<{ fetch(request: Request): Promise<Response> }>>(
    (value) => typeof value === "object" && value !== null && "fetch" in value,
  ),
})
export type Env = Readonly<z.infer<typeof envSchema>>

function telegramCredentials(
  env: Env,
  enabled: boolean,
): Readonly<{ botToken: string | undefined; chatId: string | undefined }> {
  if (enabled && (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID)) {
    throw new TypeError("Telegram is enabled but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are missing")
  }
  return { botToken: env.TELEGRAM_BOT_TOKEN, chatId: env.TELEGRAM_CHAT_ID }
}

function statelessStatus(
  config: ReturnType<typeof parseConfig>,
  results: Awaited<ReturnType<typeof checkAll>>,
): Response {
  const checkedAt = new Date().toISOString()
  return Response.json({
    refresh_seconds: config.checkIntervalSeconds,
    checked_at: checkedAt,
    targets: results.map((checked) => ({
      name: checked.target.name ?? null,
      host: checked.target.host,
      port: checked.target.port,
      resolved_ip: checked.resolvedIp ?? null,
      status: checked.result.status,
      reason: checked.result.reason,
      checked_at: checked.checkedAt,
    })),
  })
}

export const worker = {
  async fetch(request: Request, rawEnv: Env): Promise<Response> {
    const env = envSchema.parse(rawEnv)
    const config = parseConfig(env.MONITOR_CONFIG_JSON)
    telegramCredentials(env, config.telegram.enabled)
    const path = new URL(request.url).pathname
    if (request.method !== "GET" && (path === "/health" || path === "/api/status")) {
      return Response.json({ error: "method not allowed" }, { status: 405 })
    }
    if (path === "/health") return Response.json({ status: "ok" })
    if (path === "/api/status") return statelessStatus(config, await checkAll(config))
    if (path.startsWith("/api/")) return Response.json({ error: "not found" }, { status: 404 })
    return env.ASSETS.fetch(request)
  },
  async scheduled(_controller: ScheduledController, rawEnv: Env): Promise<void> {
    const env = envSchema.parse(rawEnv)
    const config = parseConfig(env.MONITOR_CONFIG_JSON)
    const results = await checkAll(config)
    const credentials = telegramCredentials(env, config.telegram.enabled)
    const failures: Error[] = []
    for (const checked of results) {
      if (checked.result.status === "normal") continue
      try {
        await notifyTelegram(config.telegram, checked, credentials)
      } catch (error) {
        failures.push(error instanceof Error ? error : new TypeError(String(error)))
      }
    }
    if (failures.length > 0) throw new AggregateError(failures, "Telegram notifications failed")
  },
} satisfies ExportedHandler<Env>

export default worker
