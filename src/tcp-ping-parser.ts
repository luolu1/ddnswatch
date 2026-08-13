import * as z from "zod"

export type CheckResult = Readonly<{ status: "normal" | "blocked" | "unknown"; reason: string }>
const nodeSchema = z
  .object({
    node_id: z.union([z.string(), z.number()]).optional(),
    id: z.union([z.string(), z.number()]).optional(),
    location: z.unknown().optional(),
    address: z.unknown().optional(),
    provider: z.unknown().optional(),
    name: z.unknown().optional(),
    head: z.unknown().optional(),
    result: z.unknown().optional(),
  })
  .loose()
const stateSchema = z
  .object({
    outstandingNodeCount: z.number().optional(),
    outstandingNodes: z.union([z.array(nodeSchema), z.record(z.string(), nodeSchema)]).optional(),
  })
  .loose()
const pollSchema = z
  .object({
    state: stateSchema.optional(),
    data: z
      .union([
        z.array(nodeSchema),
        z.object({ state: stateSchema.optional(), data: z.array(nodeSchema).optional() }).loose(),
      ])
      .optional(),
  })
  .loose()
export type ProbeNode = Readonly<z.infer<typeof nodeSchema>>
const terms = [
  "中国",
  "北京",
  "上海",
  "天津",
  "重庆",
  "河北",
  "山西",
  "辽宁",
  "吉林",
  "黑龙江",
  "江苏",
  "浙江",
  "安徽",
  "福建",
  "江西",
  "山东",
  "河南",
  "湖北",
  "湖南",
  "广东",
  "海南",
  "四川",
  "贵州",
  "云南",
  "陕西",
  "甘肃",
  "青海",
  "台湾",
  "内蒙古",
  "广西",
  "西藏",
  "宁夏",
  "新疆",
  "China",
  "china",
  "Beijing",
  "Shanghai",
  "Guangzhou",
  "Shenzhen",
  "Hong Kong",
  "香港",
  "澳门",
  "Guangdong",
  "Zhejiang",
  "Jiangsu",
  "Sichuan",
] as const
function decodeHtml(text: string): string {
  return text.replaceAll(/&(?:amp|quot|#39|lt|gt);/g, (entity) => {
    if (entity === "&amp;") return "&"
    if (entity === "&quot;") return '"'
    if (entity === "&#39;") return "'"
    if (entity === "&lt;") return "<"
    if (entity === "&gt;") return ">"
    return entity
  })
}
export function extractTcpPingTask(
  text: string,
): Readonly<{ query?: string; token?: string; intervalSeconds?: number }> {
  const decoded = decodeHtml(text)
  const value = (name: string) =>
    new RegExp(`(?:['"]?${name}['"]?)\\s*[:=]\\s*['"]([^'"]+)['"]`).exec(decoded)?.[1]
  const interval = /(?:['"]?interval_s['"]?)\s*[:=]\s*([0-9]+(?:\.[0-9]+)?)/.exec(decoded)?.[1]
  const query = value("taskStartQuery")
  const token = value("taskStartToken")
  return {
    ...(query ? { query } : {}),
    ...(token ? { token } : {}),
    ...(interval ? { intervalSeconds: Number(interval) } : {}),
  }
}
export function extractAntifloodCookie(text: string): string | undefined {
  const decoded = decodeHtml(text)
  for (const match of decoded.matchAll(/document\.cookie\s*=\s*(['"])(.*?)\1/gis)) {
    const assignment = match[2]
    if (!assignment) continue
    for (const part of assignment.split(";")) {
      const separator = part.indexOf("=")
      const name = part.slice(0, separator).trim()
      const value = part.slice(separator + 1).trim()
      if (separator >= 0 && name.toLowerCase() === "antiflood" && value) return value
    }
  }
  return /(?:^|[;\s])antiflood\s*=\s*([^;\s"']+)/i.exec(decoded)?.[1]
}
export function isMainland(node: ProbeNode): boolean {
  return terms.some((term) =>
    [node.location, node.address, node.provider, node.name, node.head]
      .map((value) => String(value ?? ""))
      .join(" ")
      .includes(term),
  )
}
export function aggregateTcpPingResults(
  items: readonly ProbeNode[],
  minimum = 3,
  threshold = 0.2,
): CheckResult {
  let success = 0
  let failure = 0
  let unknown = 0
  const mainland = items.filter(isMainland)
  for (const item of mainland) {
    const numeric = item.result === null || item.result === "" ? Number.NaN : Number(item.result)
    if (numeric === 1) failure += 1
    else if (numeric === 0 || numeric > 1) success += 1
    else unknown += 1
  }
  const reason = `source=tcp.ping.pe, cn_success=${success}, cn_failure=${failure}, cn_unknown=${unknown}, cn_total=${mainland.length}`
  if (mainland.length < minimum)
    return { status: "unknown", reason: `${reason}, insufficient mainland probes` }
  const effective = success + failure
  if (effective === 0) return { status: "unknown", reason: `${reason}, no effective probe results` }
  const rate = success / effective
  return {
    status: rate <= threshold ? "blocked" : "normal",
    reason: `${reason}, success_rate=${(rate * 100).toFixed(2)}%`,
  }
}
export function readPoll(payload: unknown, nodes: Map<string, ProbeNode>): number | undefined {
  const parsed = pollSchema.parse(payload)
  const nested = Array.isArray(parsed.data) ? undefined : parsed.data
  const state = nested?.state ?? parsed.state
  if (state?.outstandingNodes) mergeNodes(nodes, state.outstandingNodes)
  const data = Array.isArray(parsed.data) ? parsed.data : (nested?.data ?? [])
  mergeNodes(nodes, data)
  return state?.outstandingNodeCount
}
function mergeNodes(
  nodes: Map<string, ProbeNode>,
  incoming: readonly ProbeNode[] | Readonly<Record<string, ProbeNode>>,
): void {
  const entries = Array.isArray(incoming)
    ? incoming.map((node) => [undefined, node] as const)
    : Object.entries(incoming)
  for (const [fallback, node] of entries) {
    const identity = node.node_id ?? node.id ?? fallback ?? String(nodes.size)
    const key = String(identity)
    nodes.set(key, { ...(fallback ? { node_id: identity } : {}), ...nodes.get(key), ...node })
  }
}
