import * as z from "zod"
import type { HttpFetch } from "./tcp-ping"

const responseSchema = z.object({
  Answer: z.array(z.object({ type: z.number(), data: z.string() })).optional(),
})
const ipv4 = /^(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}$/
export async function resolveIpv4(
  host: string,
  fetcher: HttpFetch = fetch,
): Promise<string | undefined> {
  if (ipv4.test(host)) return host
  const response = await fetcher(
    `https://cloudflare-dns.com/dns-query?name=${encodeURIComponent(host)}&type=A`,
    { headers: { Accept: "application/dns-json" } },
  )
  if (!response.ok) throw new TypeError(`Cloudflare DoH HTTP ${response.status}`)
  return responseSchema
    .parse(await response.json())
    .Answer?.find((answer) => answer.type === 1 && ipv4.test(answer.data))?.data
}
