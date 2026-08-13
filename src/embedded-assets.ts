export type EmbeddedAssetSources = Readonly<{
  html: string
  javascript: string
  css: string
}>

export type AssetFetcher = Readonly<{
  fetch(request: Request): Promise<Response>
}>

const CONTENT_TYPES = {
  html: "text/html; charset=utf-8",
  javascript: "text/javascript; charset=utf-8",
  css: "text/css; charset=utf-8",
} as const

export function createEmbeddedAssets(sources: EmbeddedAssetSources): AssetFetcher {
  return {
    async fetch(request: Request): Promise<Response> {
      const path = new URL(request.url).pathname
      if (path === "/" || path === "/index.html") {
        return new Response(sources.html, { headers: { "Content-Type": CONTENT_TYPES.html } })
      }
      if (path === "/app.js") {
        return new Response(sources.javascript, {
          headers: { "Content-Type": CONTENT_TYPES.javascript },
        })
      }
      if (path === "/style.css") {
        return new Response(sources.css, { headers: { "Content-Type": CONTENT_TYPES.css } })
      }
      return new Response("Not Found", { status: 404 })
    },
  }
}
