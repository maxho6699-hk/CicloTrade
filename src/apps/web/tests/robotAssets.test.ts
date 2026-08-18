import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'
import { inflateSync } from 'node:zlib'
import { CICLO_TIER_ASSETS, tierFromPlan } from '../src/components/paper/cicloCoreModel.ts'

const root = new URL('../public/assets/robot/', import.meta.url)
const manifestUrl = new URL('robot-family-manifest.json', root)
const accountCss = readFileSync(new URL('../src/styles/account-center.css', import.meta.url), 'utf8')
const deliberationCss = readFileSync(new URL('../src/styles/deliberation.css', import.meta.url), 'utf8')

function paeth(left: number, up: number, upperLeft: number) {
  const estimate = left + up - upperLeft
  const leftDistance = Math.abs(estimate - left)
  const upDistance = Math.abs(estimate - up)
  const upperLeftDistance = Math.abs(estimate - upperLeft)
  if (leftDistance <= upDistance && leftDistance <= upperLeftDistance) return left
  return upDistance <= upperLeftDistance ? up : upperLeft
}

const CRC_TABLE = Array.from({ length: 256 }, (_, value) => {
  let crc = value
  for (let bit = 0; bit < 8; bit += 1) crc = (crc & 1) === 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1
  return crc >>> 0
})

function crc32(...parts: Buffer[]) {
  let crc = 0xffffffff
  for (const part of parts) for (const byte of part) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8)
  return (crc ^ 0xffffffff) >>> 0
}

function pngChunk(type: string, data: Buffer) {
  const typeBytes = Buffer.from(type, 'ascii')
  const chunk = Buffer.alloc(data.length + 12)
  chunk.writeUInt32BE(data.length, 0)
  typeBytes.copy(chunk, 4)
  data.copy(chunk, 8)
  chunk.writeUInt32BE(crc32(typeBytes, data), data.length + 8)
  return chunk
}

function readRgbaAlphaBounds(bytes: Buffer): [number, number, number, number] {
  assert.ok(bytes.length <= 5 * 1024 * 1024, 'robot PNG exceeds the validation size limit')
  assert.deepEqual([...bytes.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10])
  let offset = 8
  let width = 0
  let height = 0
  let seenIhdr = false
  let seenPlte = false
  let seenIdat = false
  let idatClosed = false
  let seenIend = false
  let idatBytes = 0
  const idat: Buffer[] = []
  while (offset < bytes.length) {
    assert.ok(offset + 12 <= bytes.length, 'PNG chunk header is out of bounds')
    const length = bytes.readUInt32BE(offset)
    const dataStart = offset + 8
    const dataEnd = dataStart + length
    const crcEnd = dataEnd + 4
    assert.ok(Number.isSafeInteger(dataEnd) && crcEnd <= bytes.length, 'PNG chunk data is out of bounds')
    const typeBytes = bytes.subarray(offset + 4, offset + 8)
    const type = typeBytes.toString('ascii')
    assert.match(type, /^[A-Za-z]{4}$/, 'PNG chunk type is invalid')
    const data = bytes.subarray(dataStart, dataEnd)
    assert.equal(bytes.readUInt32BE(dataEnd), crc32(typeBytes, data), `${type} CRC mismatch`)
    if (type === 'IHDR') {
      assert.equal(offset, 8, 'IHDR must be the first PNG chunk')
      assert.equal(seenIhdr, false, 'PNG contains duplicate IHDR chunks')
      assert.equal(length, 13, 'IHDR must contain exactly 13 bytes')
      seenIhdr = true
      width = data.readUInt32BE(0)
      height = data.readUInt32BE(4)
      assert.ok(width > 0 && height > 0 && width <= 2048 && height <= 2048, 'robot PNG dimensions exceed validation bounds')
      assert.equal(data[8], 8, 'robot PNG must use 8-bit channels')
      assert.equal(data[9], 6, 'robot PNG must use RGBA color type')
      assert.equal(data[10], 0, 'robot PNG uses an unsupported compression method')
      assert.equal(data[11], 0, 'robot PNG uses an unsupported filter method')
      assert.equal(data[12], 0, 'robot PNG must be non-interlaced')
    } else if (type === 'PLTE') {
      assert.equal(seenIhdr, true, 'PLTE cannot appear before IHDR')
      assert.equal(seenIdat, false, 'PLTE must appear before IDAT')
      assert.equal(seenPlte, false, 'PNG contains duplicate PLTE chunks')
      assert.ok(length > 0 && length <= 768 && length % 3 === 0, 'PLTE length is invalid')
      seenPlte = true
    } else if (type === 'IDAT') {
      assert.equal(seenIhdr, true, 'IDAT cannot appear before IHDR')
      assert.equal(idatClosed, false, 'IDAT chunks must be consecutive')
      seenIdat = true
      idatBytes += length
      assert.ok(idatBytes <= 5 * 1024 * 1024, 'PNG IDAT data exceeds validation bounds')
      idat.push(data)
    } else {
      if (seenIdat) idatClosed = true
      if (type === 'IEND') {
        assert.equal(length, 0, 'IEND must be empty')
        assert.equal(seenIhdr && seenIdat, true, 'IEND requires IHDR and IDAT')
        seenIend = true
        offset = crcEnd
        assert.equal(offset, bytes.length, 'PNG has trailing bytes after IEND')
        break
      }
      assert.equal(/^[A-Z]/.test(type), false, `unsupported critical PNG chunk ${type}`)
    }
    offset = crcEnd
  }
  assert.equal(seenIend, true, 'PNG is missing IEND')
  assert.ok(seenIhdr && seenIdat && width > 0 && height > 0 && idat.length > 0, 'robot PNG chunks are incomplete')
  const stride = width * 4
  const expectedLength = height * (stride + 1)
  assert.ok(Number.isSafeInteger(stride) && Number.isSafeInteger(expectedLength) && expectedLength <= 20 * 1024 * 1024, 'PNG decoded data exceeds validation bounds')
  const inflated = inflateSync(Buffer.concat(idat), { maxOutputLength: expectedLength })
  assert.equal(inflated.length, expectedLength)
  let source = 0
  let previous = Buffer.alloc(stride)
  let minX = width
  let minY = height
  let maxX = -1
  let maxY = -1
  for (let y = 0; y < height; y += 1) {
    const filter = inflated[source]
    source += 1
    const row = Buffer.alloc(stride)
    for (let x = 0; x < stride; x += 1) {
      const raw = inflated[source + x]
      const left = x >= 4 ? row[x - 4] : 0
      const up = previous[x]
      const upperLeft = x >= 4 ? previous[x - 4] : 0
      let predictor = 0
      if (filter === 1) predictor = left
      else if (filter === 2) predictor = up
      else if (filter === 3) predictor = Math.floor((left + up) / 2)
      else if (filter === 4) predictor = paeth(left, up, upperLeft)
      else assert.equal(filter, 0, `unsupported PNG filter ${filter}`)
      row[x] = (raw + predictor) & 0xff
    }
    for (let x = 0; x < width; x += 1) {
      if (row[x * 4 + 3] === 0) continue
      minX = Math.min(minX, x)
      minY = Math.min(minY, y)
      maxX = Math.max(maxX, x)
      maxY = Math.max(maxY, y)
    }
    previous = row
    source += stride
  }
  assert.ok(maxX >= minX && maxY >= minY, 'robot PNG has no visible alpha pixels')
  return [minX, minY, maxX + 1, maxY + 1]
}

test('LV1-LV4 robot assets are verified RGBA files with monotonic body progression', () => {
  assert.equal(existsSync(manifestUrl), true, 'robot family manifest is missing')
  const manifest = JSON.parse(readFileSync(manifestUrl, 'utf8')) as {
    verdict: string
    hard_violations: string[]
    scores: Record<string, number>
    levels: Array<{ level: number; file: string; sha256: string; content_bbox: [number, number, number, number] }>
  }
  assert.equal(manifest.verdict, 'PASS')
  assert.deepEqual(manifest.hard_violations, [])
  assert.ok(Object.values(manifest.scores).every((score) => score >= 9))
  assert.deepEqual(manifest.levels.map((item) => item.level), [1, 2, 3, 4])
  const widths: number[] = []
  const heights: number[] = []
  const hashes = new Set<string>()
  for (const item of manifest.levels) {
    const fileUrl = new URL(item.file, root)
    const bytes = readFileSync(fileUrl)
    assert.equal(bytes.readUInt32BE(16), 1024)
    assert.equal(bytes.readUInt32BE(20), 1024)
    assert.equal(bytes[25], 6, `${item.file} is not RGBA PNG`)
    const hash = createHash('sha256').update(bytes).digest('hex')
    assert.equal(hash, item.sha256)
    assert.deepEqual(readRgbaAlphaBounds(bytes), item.content_bbox)
    hashes.add(hash)
    widths.push(item.content_bbox[2] - item.content_bbox[0])
    heights.push(item.content_bbox[3] - item.content_bbox[1])
  }
  assert.equal(hashes.size, 4)
  assert.deepEqual(widths, [340, 400, 470, 520])
  assert.deepEqual(heights, [700, 770, 840, 910])
})

test('robot PNG validation rejects corrupted, incomplete, and trailing streams', () => {
  const valid = readFileSync(new URL('robot-lv1.png', root))
  const corruptCrc = Buffer.from(valid)
  corruptCrc[29] ^= 1
  assert.throws(() => readRgbaAlphaBounds(corruptCrc), /CRC/)
  assert.throws(() => readRgbaAlphaBounds(valid.subarray(0, valid.length - 12)), /IEND/)
  assert.throws(() => readRgbaAlphaBounds(Buffer.concat([valid, Buffer.from([0])])), /trailing/)
  const oversizedChunk = Buffer.from(valid)
  oversizedChunk.writeUInt32BE(0xffffffff, 8)
  assert.throws(() => readRgbaAlphaBounds(oversizedChunk), /bounds/)
  const iendOffset = valid.length - 12
  const plteAfterIdat = Buffer.concat([valid.subarray(0, iendOffset), pngChunk('PLTE', Buffer.from([0, 0, 0])), valid.subarray(iendOffset)])
  assert.throws(() => readRgbaAlphaBounds(plteAfterIdat), /PLTE.*before IDAT/)
})

test('membership plans map to one authoritative robot asset per tier', () => {
  assert.deepEqual(CICLO_TIER_ASSETS, {
    free: '/assets/robot/robot-lv1.png',
    standard: '/assets/robot/robot-lv2.png',
    advanced: '/assets/robot/robot-lv3.png',
    professional: '/assets/robot/robot-lv4.png',
    custom: '/assets/robot/robot-lv4.png',
  })
  assert.equal(tierFromPlan('免费版'), 'free')
  assert.equal(tierFromPlan('标准版'), 'standard')
  assert.equal(tierFromPlan('高级版'), 'advanced')
  assert.equal(tierFromPlan('专业版'), 'professional')
  assert.equal(tierFromPlan('定制版'), 'custom')
})

test('account robot stage keeps effects behind the body and normalizes tier occupancy', () => {
  assert.match(accountCss, /\.profile-agent-stage \.ciclo-core-free \.ciclo-core-hero-image\{transform:scale\(1\.08\)\}/)
  assert.match(accountCss, /\.profile-agent-stage \.ciclo-core-standard \.ciclo-core-hero-image\{transform:scale\(1\.04\)\}/)
  assert.match(accountCss, /\.profile-agent-stage \.ciclo-core-advanced \.ciclo-core-hero-image\{transform:scale\(1\)\}/)
  assert.match(accountCss, /\.profile-agent-stage :is\(\.ciclo-core-professional,\.ciclo-core-custom\) \.ciclo-core-hero-image\{transform:scale\(\.96\)\}/)
  assert.match(accountCss, /\.profile-agent-stage \.ciclo-core-image-orbits\{z-index:2;/)
  assert.match(accountCss, /\.profile-agent-stage \.ciclo-core-image-particles\{z-index:2;/)
  assert.match(accountCss, /\.profile-agent-stage \.ciclo-core-energy-field\{display:none\}/)
  assert.match(accountCss, /\.profile-agent-badge\{[^}]*top:18px;[^}]*right:18px;/)
  assert.match(accountCss, /\.profile-evolution-path small\{color:color-mix\(in srgb,var\(--muted\) 72%,var\(--text\)\);font-size:12px\}/)
})

test('deliberation robot stage keeps decorative effects behind the body', () => {
  assert.match(deliberationCss, /\.app-shell \.deliberation-robot-stage \.ciclo-core-image-orbits \{ z-index: 2;/)
  assert.match(deliberationCss, /\.app-shell \.deliberation-robot-stage \.ciclo-core-image-particles \{ z-index: 2;/)
  assert.match(deliberationCss, /\.app-shell \.deliberation-robot-stage \.ciclo-core-energy-field \{ display: none; \}/)
})
