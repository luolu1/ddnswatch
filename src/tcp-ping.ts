import * as z from "zod"
import type { Target } from "./config"
import {
  aggregateTcpPingResults,
  type CheckResult,
  extractAntifloodCookie,
  extractTcpPingTask,
  isMainland,
  type ProbeNode,
  readPoll,
} from "./tcp-ping-parser"

export type { CheckResult } from "./tcp-ping-parser"
export {
  aggregateTcpPingResults,
  extractAntifloodCookie,
  extractTcpPingTask,
} from "./tcp-ping-parser"
export type HttpFetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
const startSchema = z.object({
  ok: z.literal(true),
  data: z.object({ stream_id: z.union([z.string(), z.number()]) }),
})
export type TcpOptions = Readonly<{
  baseUrl: string
  minCnProbes: number
  blockedSuccessRate: number
  timeoutSeconds: number
  maxPolls: number
  pollIntervalSeconds: number
  fetcher: HttpFetch
  delay: (milliseconds: number) => Promise<void>
}>
export async function checkTcpPing(
  target: Target,
  ip: string | undefined,
  options: TcpOptions,
): Promise<CheckResult> {
  if (!ip) return { status: "unknown", reason: "tcp.ping.pe target has no resolved IPv4 address" }
  const base = options.baseUrl.replace(/\/$/, "")
  const pageUrl = `${base}/${encodeURIComponent(`${ip}:${target.port}`)}`
  const common = {
    "User-Agent": "Mozilla/5.0 (DDNSWatch; tcp.ping.pe)",
    Origin: base,
    "X-Requested-With": "XMLHttpRequest",
  }
  const request: HttpFetch = (input, init) =>
    options.fetcher(input, { ...init, signal: AbortSignal.timeout(options.timeoutSeconds * 1000) })
  let streamId: string | undefined
  try {
    let page = await request(pageUrl, { headers: { ...common, Referer: `${base}/` } })
    let html = await page.text()
    let task = extractTcpPingTask(html)
    let cookie: string | undefined
    if (!task.query || !task.token) {
      cookie = extractAntifloodCookie(html)
      page = await request(`${pageUrl}?browsercheck=ok`, {
        headers: {
          ...common,
          Referer: pageUrl,
          ...(cookie ? { Cookie: `antiflood=${cookie}` } : {}),
        },
      })
      html = await page.text()
      task = extractTcpPingTask(html)
    }
    if (!page.ok)
      return { status: "unknown", reason: `tcp.ping.pe browser validation HTTP ${page.status}` }
    if (!task.query || !task.token)
      return { status: "unknown", reason: "tcp.ping.pe browser validation/token missing" }
    const headers = {
      ...common,
      Referer: `${pageUrl}?browsercheck=ok`,
      "Content-Type": "application/x-www-form-urlencoded",
      ...(cookie ? { Cookie: `antiflood=${cookie}` } : {}),
    }
    const started = await request(`${base}/ajax_startTask_v1.php`, {
      method: "POST",
      headers,
      body: new URLSearchParams({ query: task.query, start_token: task.token }),
    })
    const parsed = startSchema.safeParse(await started.json())
    if (!parsed.success) return { status: "unknown", reason: "tcp.ping.pe task start failed" }
    streamId = String(parsed.data.data.stream_id)
    const nodes = new Map<string, ProbeNode>()
    const interval = task.intervalSeconds ?? options.pollIntervalSeconds
    for (let poll = 1; poll <= options.maxPolls; poll += 1) {
      const response = await request(
        `${base}/ajax_getPingResults_v2.php?type=tcp&totalPolls=${poll}&stream_id=${encodeURIComponent(streamId)}`,
        { headers },
      )
      const outstanding = readPoll(await response.json(), nodes)
      const completed = [...nodes.values()].filter(
        (node) => isMainland(node) && node.result !== undefined,
      ).length
      const pending = [...nodes.values()].some(
        (node) => isMainland(node) && node.result === undefined,
      )
      if (
        outstanding === 0 ||
        (outstanding !== undefined && completed >= options.minCnProbes && !pending)
      )
        return aggregateTcpPingResults(
          [...nodes.values()],
          options.minCnProbes,
          options.blockedSuccessRate,
        )
      if (poll < options.maxPolls) await options.delay(Math.max(0, interval) * 1000)
    }
    return { status: "unknown", reason: "tcp.ping.pe outstanding nodes did not finish" }
  } catch (error) {
    return {
      status: "unknown",
      reason: `tcp.ping.pe check failure: ${error instanceof Error ? error.message : String(error)}`,
    }
  } finally {
    if (streamId) {
      try {
        await request(`${base}/ajax_stopTask.php?stream_id=${encodeURIComponent(streamId)}`, {
          headers: common,
        })
      } catch (error) {
        if (error instanceof Error) console.warn("tcp.ping.pe stop failed", error.message)
      }
    }
  }
}
