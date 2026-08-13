import { createEmbeddedAssets } from "./embedded-assets"
import { createWorker } from "./index"

declare const DASHBOARD_HTML: string
declare const DASHBOARD_JAVASCRIPT: string
declare const DASHBOARD_CSS: string

const worker = createWorker(
  createEmbeddedAssets({
    html: DASHBOARD_HTML,
    javascript: DASHBOARD_JAVASCRIPT,
    css: DASHBOARD_CSS,
  }),
)

export default worker
