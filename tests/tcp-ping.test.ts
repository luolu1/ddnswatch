import { describe, expect, it } from "vitest"
import { checkTcpPing, type HttpFetch, type TcpOptions } from "../src/tcp-ping"

const target = { host: "example.com", port: 443 } as const

function options(fetcher: HttpFetch): TcpOptions {
  return {
    baseUrl: "https://tcp.ping.pe",
    minCnProbes: 2,
    blockedSuccessRate: 0.2,
    timeoutSeconds: 20,
    maxPolls: 2,
    pollIntervalSeconds: 0,
    fetcher,
    delay: async () => undefined,
  }
}

describe("tcp.ping.pe wire flow", () => {
  it("forwards antiflood, uses dynamic token, merges polls, and stops the stream", async () => {
    const recorded: Request[] = []
    let page = 0
    let poll = 0
    const fetcher: HttpFetch = async (input, init) => {
      const request = new Request(input, init)
      recorded.push(request)
      if (request.url.includes("ajax_stopTask")) return Response.json({})
      if (request.url.includes("ajax_getPingResults")) {
        poll += 1
        return poll === 1
          ? Response.json({
              state: {
                outstandingNodeCount: 1,
                outstandingNodes: [{ node_id: "cn-a", location: "China" }],
              },
              data: [{ node_id: "cn-a", result: 0 }],
            })
          : Response.json({
              state: { outstandingNodeCount: 0 },
              data: [{ node_id: "cn-b", location: "中国", result: 1 }],
            })
      }
      if (request.url.includes("ajax_startTask")) {
        expect(await request.text()).toContain("start_token=dynamic")
        expect(request.headers.get("Cookie")).toBe("antiflood=browser-token")
        return Response.json({ ok: true, data: { stream_id: "stream-one" } })
      }
      page += 1
      if (page === 1)
        return new Response('document.cookie="foo=x; antiflood=browser-token; path=/"')
      expect(request.headers.get("Cookie")).toBe("antiflood=browser-token")
      return new Response("taskStartQuery='ip:443'; taskStartToken='dynamic'; interval_s=0")
    }

    const result = await checkTcpPing(target, "198.51.100.10", options(fetcher))

    expect(result.status).toBe("normal")
    expect(
      recorded.some(
        (request) => request.url.includes("ajax_stopTask") && request.url.includes("stream-one"),
      ),
    ).toBe(true)
  })

  it("stops a created stream when polling fails", async () => {
    const calls: string[] = []
    const fetcher: HttpFetch = async (input) => {
      const url = String(input)
      calls.push(url)
      if (url.includes("ajax_stopTask")) return Response.json({})
      if (url.includes("ajax_getPingResults")) throw new TypeError("poll failed")
      if (url.includes("ajax_startTask"))
        return Response.json({ ok: true, data: { stream_id: "cleanup" } })
      return new Response("taskStartQuery='ip:443'; taskStartToken='token'; interval_s=0")
    }

    const result = await checkTcpPing(target, "198.51.100.10", options(fetcher))

    expect(result.status).toBe("unknown")
    expect(calls.some((url) => url.includes("ajax_stopTask") && url.includes("cleanup"))).toBe(true)
  })
})
