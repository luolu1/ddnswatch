import { mkdir, rm } from "node:fs/promises"
import { join } from "node:path"

const ROOT = join(import.meta.dir, "..")
const RELEASE_DIRECTORY = join(ROOT, "release")
const WORKER_NAME = "_worker.js"
const ZIP_NAME = "ddnswatch-cloudflare-upload.zip"
const CHECKSUM_NAME = `${ZIP_NAME}.sha256`

function uint16(value: number): Uint8Array {
  return Uint8Array.of(value & 0xff, (value >>> 8) & 0xff)
}

function uint32(value: number): Uint8Array {
  return Uint8Array.of(value & 0xff, (value >>> 8) & 0xff, (value >>> 16) & 0xff, value >>> 24)
}

function concat(parts: readonly Uint8Array[]): Uint8Array {
  const length = parts.reduce((total, part) => total + part.length, 0)
  const output = new Uint8Array(length)
  let offset = 0
  for (const part of parts) {
    output.set(part, offset)
    offset += part.length
  }
  return output
}

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff
  for (const byte of bytes) {
    crc ^= byte
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1))
  }
  return (crc ^ 0xffffffff) >>> 0
}

function createZip(name: string, contents: Uint8Array): Uint8Array {
  const encoder = new TextEncoder()
  const fileName = encoder.encode(name)
  const checksum = crc32(contents)
  const localHeader = concat([
    uint32(0x04034b50),
    uint16(20),
    uint16(0x0800),
    uint16(0),
    uint16(0),
    uint16(0x0021),
    uint32(checksum),
    uint32(contents.length),
    uint32(contents.length),
    uint16(fileName.length),
    uint16(0),
    fileName,
  ])
  const centralDirectory = concat([
    uint32(0x02014b50),
    uint16(0x0314),
    uint16(20),
    uint16(0x0800),
    uint16(0),
    uint16(0),
    uint16(0x0021),
    uint32(checksum),
    uint32(contents.length),
    uint32(contents.length),
    uint16(fileName.length),
    uint16(0),
    uint16(0),
    uint16(0),
    uint16(0),
    uint32(0x81a40000),
    uint32(0),
    fileName,
  ])
  const end = concat([
    uint32(0x06054b50),
    uint16(0),
    uint16(0),
    uint16(1),
    uint16(1),
    uint32(centralDirectory.length),
    uint32(localHeader.length + contents.length),
    uint16(0),
  ])
  return concat([localHeader, contents, centralDirectory, end])
}

async function readAsset(name: string): Promise<string> {
  return Bun.file(join(ROOT, "public", name)).text()
}

async function main(): Promise<void> {
  const [html, javascript, css] = await Promise.all([
    readAsset("index.html"),
    readAsset("app.js"),
    readAsset("style.css"),
  ])
  const build = await Bun.build({
    entrypoints: [join(ROOT, "src", "direct-upload.ts")],
    target: "browser",
    format: "esm",
    splitting: false,
    packages: "bundle",
    minify: true,
    sourcemap: "none",
    env: "disable",
    define: {
      DASHBOARD_HTML: JSON.stringify(html),
      DASHBOARD_JAVASCRIPT: JSON.stringify(javascript),
      DASHBOARD_CSS: JSON.stringify(css),
    },
  })
  const output = build.outputs[0]
  if (!build.success || output === undefined) throw new TypeError("Direct-upload bundle failed")
  const worker = new Uint8Array(await output.arrayBuffer())
  const archive = createZip(WORKER_NAME, worker)
  const hash = new Bun.CryptoHasher("sha256").update(archive).digest("hex")

  await rm(RELEASE_DIRECTORY, { recursive: true, force: true })
  await mkdir(RELEASE_DIRECTORY, { recursive: true })
  await Promise.all([
    Bun.write(join(RELEASE_DIRECTORY, WORKER_NAME), worker),
    Bun.write(join(RELEASE_DIRECTORY, ZIP_NAME), archive),
    Bun.write(join(RELEASE_DIRECTORY, CHECKSUM_NAME), `${hash}  ${ZIP_NAME}\n`),
  ])
  console.log(`Built release/${ZIP_NAME} (${archive.length} bytes)`)
  console.log(`SHA-256 ${hash}`)
}

await main()
