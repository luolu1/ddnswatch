import type { Config, Target } from "./config"
import { resolveIpv4 } from "./dns"
import { type CheckResult, checkTcpPing, type HttpFetch } from "./tcp-ping"
import type { CheckedTarget } from "./telegram"

export type MonitorDependencies = Readonly<{
  fetcher?: HttpFetch
  delay?: (milliseconds: number) => Promise<void>
  now?: () => Date
}>
export async function checkTarget(
  target: Target,
  config: Config,
  dependencies: MonitorDependencies = {},
): Promise<CheckedTarget> {
  const fetcher = dependencies.fetcher ?? fetch
  let resolvedIp: string | undefined
  let result: CheckResult
  try {
    resolvedIp = await resolveIpv4(target.host, fetcher)
    result = resolvedIp
      ? await checkTcpPing(target, resolvedIp, {
          baseUrl: config.tcpPing.baseUrl,
          minCnProbes: config.tcpPing.minCnProbes,
          blockedSuccessRate: config.tcpPing.blockedSuccessRate,
          timeoutSeconds: config.tcpPing.timeoutSeconds,
          maxPolls: config.tcpPing.maxPolls,
          pollIntervalSeconds: config.tcpPing.pollIntervalSeconds,
          fetcher,
          delay: dependencies.delay ?? ((milliseconds) => scheduler.wait(milliseconds)),
        })
      : { status: "unknown", reason: "resolve failure: no IPv4 address" }
  } catch (error) {
    result = {
      status: "unknown",
      reason: `resolve failure: ${error instanceof Error ? error.message : String(error)}`,
    }
  }
  return {
    target,
    ...(resolvedIp ? { resolvedIp } : {}),
    result,
    checkedAt: (dependencies.now ?? (() => new Date()))().toISOString(),
  }
}
export async function checkAll(
  config: Config,
  dependencies: MonitorDependencies = {},
): Promise<readonly CheckedTarget[]> {
  const results: CheckedTarget[] = []
  for (const target of config.targets) results.push(await checkTarget(target, config, dependencies))
  return results
}
