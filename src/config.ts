import * as z from "zod"

const targetSchema = z
  .object({
    host: z.string().optional(),
    domain: z.string().optional(),
    port: z.number().int().min(1).max(65535),
    name: z.string().optional(),
  })
  .refine((value) => value.host ?? value.domain, "target requires host or domain")
const schema = z.object({
  check_interval_seconds: z.literal(60).default(60),
  targets: z.array(targetSchema).max(2).default([]),
  tcp_ping: z
    .object({
      base_url: z.string().url().default("https://tcp.ping.pe"),
      min_cn_probes: z.number().int().min(1).default(3),
      blocked_success_rate: z.number().min(0).max(1).default(0.2),
      timeout_seconds: z.number().positive().default(20),
      max_polls: z.number().int().min(1).max(15).default(15),
      poll_interval_seconds: z.number().min(0).default(3),
    })
    .strict()
    .prefault({}),
  telegram: z
    .object({
      enabled: z.boolean().default(false),
    })
    .strict()
    .prefault({}),
})
export type Target = Readonly<{ host: string; port: number; name?: string }>
export type Config = Readonly<{
  checkIntervalSeconds: number
  targets: readonly Target[]
  tcpPing: Readonly<{
    baseUrl: string
    minCnProbes: number
    blockedSuccessRate: number
    timeoutSeconds: number
    maxPolls: number
    pollIntervalSeconds: number
  }>
  telegram: Readonly<{ enabled: boolean }>
}>
export function parseConfig(raw: string | undefined): Config {
  const untrusted: unknown = raw ? JSON.parse(raw) : {}
  const parsed = schema.parse(untrusted)
  return {
    checkIntervalSeconds: parsed.check_interval_seconds,
    targets: parsed.targets.map((target) => ({
      host: target.host ?? target.domain ?? "",
      port: target.port,
      ...(target.name === undefined ? {} : { name: target.name }),
    })),
    tcpPing: {
      baseUrl: parsed.tcp_ping.base_url,
      minCnProbes: parsed.tcp_ping.min_cn_probes,
      blockedSuccessRate: parsed.tcp_ping.blocked_success_rate,
      timeoutSeconds: parsed.tcp_ping.timeout_seconds,
      maxPolls: parsed.tcp_ping.max_polls,
      pollIntervalSeconds: parsed.tcp_ping.poll_interval_seconds,
    },
    telegram: { enabled: parsed.telegram.enabled },
  }
}
