import * as z from "zod"
import type { Config, Target } from "./config"
import type { CheckResult, HttpFetch } from "./tcp-ping"

const telegramResponse = z.object({ ok: z.literal(true) }).loose()
export type CheckedTarget = Readonly<{
  target: Target
  resolvedIp?: string
  result: CheckResult
  checkedAt: string
}>
export type TelegramCredentials = Readonly<{
  botToken: string | undefined
  chatId: string | undefined
}>
export async function notifyTelegram(
  config: Config["telegram"],
  checked: CheckedTarget,
  credentials: TelegramCredentials,
  fetcher: HttpFetch = fetch,
): Promise<void> {
  if (!config.enabled) return
  const token = credentials.botToken
  const chatId = credentials.chatId
  if (!token || !chatId)
    throw new TypeError("Telegram is enabled but TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are missing")
  const text = [
    `🚨 ${checked.result.status === "blocked" ? "被墙" : "未知"}`,
    `主机: ${checked.target.name ?? checked.target.host} (${checked.target.host})`,
    `解析目标IP: ${checked.resolvedIp ?? "未解析"}`,
    `端口: ${checked.target.port}`,
    `状态: ${checked.result.status}`,
    `原因: ${checked.result.reason}`,
    `时间: ${checked.checkedAt}`,
  ].join("\n")
  const response = await fetcher(`https://api.telegram.org/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text }),
    signal: AbortSignal.timeout(10_000),
  })
  if (!response.ok) throw new TypeError(`Telegram HTTP ${response.status}`)
  telegramResponse.parse(await response.json())
}
