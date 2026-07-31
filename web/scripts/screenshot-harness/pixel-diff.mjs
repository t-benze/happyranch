/**
 * Dependency-free PNG pixel-diff for the THR-105 Todos fidelity evidence
 * (TASK-3834). Decodes two same-format PNGs (8-bit, color type 2 RGB or 6
 * RGBA, non-interlaced — what playwright-cli screenshots produce) using only
 * node:zlib, compares pixel-by-pixel, and writes a red-highlighted diff PNG.
 *
 * Evidence-only tooling under web/scripts/ — not shipped app code.
 *
 * Usage: node scripts/screenshot-harness/pixel-diff.mjs <a.png> <b.png> [out-diff.png]
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { inflateSync, deflateSync, crc32 } from 'node:zlib';

const SIG = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

function readChunks(buf) {
  if (!buf.subarray(0, 8).equals(SIG)) throw new Error('not a PNG');
  const chunks = [];
  let off = 8;
  while (off < buf.length) {
    const len = buf.readUInt32BE(off);
    const type = buf.toString('ascii', off + 4, off + 8);
    const data = buf.subarray(off + 8, off + 8 + len);
    chunks.push({ type, data });
    off += 8 + len + 4; // + CRC
  }
  return chunks;
}

function paeth(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a), pb = Math.abs(p - b), pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) return a;
  if (pb <= pc) return b;
  return c;
}

/** Decode a PNG into { width, height, channels, pixels: Uint8Array (row-major, `channels` bytes/px) }. */
export function decodePNG(buf) {
  const chunks = readChunks(buf);
  const ihdr = chunks.find((c) => c.type === 'IHDR').data;
  const width = ihdr.readUInt32BE(0);
  const height = ihdr.readUInt32BE(4);
  const bitDepth = ihdr.readUInt8(8);
  const colorType = ihdr.readUInt8(9);
  const interlace = ihdr.readUInt8(12);
  if (bitDepth !== 8 || interlace !== 0 || (colorType !== 2 && colorType !== 6)) {
    throw new Error(`unsupported PNG: bitDepth=${bitDepth} colorType=${colorType} interlace=${interlace}`);
  }
  const channels = colorType === 6 ? 4 : 3;
  const idat = Buffer.concat(chunks.filter((c) => c.type === 'IDAT').map((c) => c.data));
  const raw = inflateSync(idat);

  const stride = width * channels;
  const pixels = new Uint8Array(height * stride);
  let rawOff = 0;
  const prevRow = new Uint8Array(stride);
  for (let y = 0; y < height; y++) {
    const filter = raw[rawOff++];
    const row = raw.subarray(rawOff, rawOff + stride);
    rawOff += stride;
    const outRow = pixels.subarray(y * stride, (y + 1) * stride);
    for (let x = 0; x < stride; x++) {
      const rawByte = row[x];
      const a = x >= channels ? outRow[x - channels] : 0;
      const b = prevRow[x];
      const c = x >= channels ? prevRow[x - channels] : 0;
      let val;
      switch (filter) {
        case 0: val = rawByte; break;
        case 1: val = rawByte + a; break;
        case 2: val = rawByte + b; break;
        case 3: val = rawByte + Math.floor((a + b) / 2); break;
        case 4: val = rawByte + paeth(a, b, c); break;
        default: throw new Error(`unsupported filter type ${filter}`);
      }
      outRow[x] = val & 0xff;
    }
    prevRow.set(outRow);
  }
  return { width, height, channels, pixels };
}

function chunk(type, data) {
  const typeBuf = Buffer.from(type, 'ascii');
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length, 0);
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0);
  return Buffer.concat([len, typeBuf, data, crcBuf]);
}

/** Encode an RGB (3 channel) image to a PNG buffer (filter type 0 per row). */
export function encodePNG(width, height, rgbPixels) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr.writeUInt8(8, 8); // bit depth
  ihdr.writeUInt8(2, 9); // color type: RGB
  ihdr.writeUInt8(0, 10);
  ihdr.writeUInt8(0, 11);
  ihdr.writeUInt8(0, 12);

  const stride = width * 3;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y++) {
    raw[y * (stride + 1)] = 0; // filter: none
    rgbPixels.copy(raw, y * (stride + 1) + 1, y * stride, (y + 1) * stride);
  }
  const idat = deflateSync(raw);

  return Buffer.concat([
    SIG,
    chunk('IHDR', ihdr),
    chunk('IDAT', idat),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

/**
 * Compare two decoded images of IDENTICAL dimensions. A pixel counts as
 * "different" when any RGB channel differs by more than `threshold` (0-255).
 * Alpha is ignored for the diff (screenshots are opaque).
 */
export function diffDecoded(a, b, { threshold = 12 } = {}) {
  if (a.width !== b.width || a.height !== b.height) {
    throw new Error(`dimension mismatch: ${a.width}x${a.height} vs ${b.width}x${b.height}`);
  }
  const { width, height } = a;
  const diffRgb = Buffer.alloc(width * height * 3);
  let diffCount = 0;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const ai = (y * width + x) * a.channels;
      const bi = (y * width + x) * b.channels;
      const dr = Math.abs(a.pixels[ai] - b.pixels[bi]);
      const dg = Math.abs(a.pixels[ai + 1] - b.pixels[bi + 1]);
      const db = Math.abs(a.pixels[ai + 2] - b.pixels[bi + 2]);
      const diff = dr > threshold || dg > threshold || db > threshold;
      const oi = (y * width + x) * 3;
      if (diff) {
        diffCount++;
        diffRgb[oi] = 255;
        diffRgb[oi + 1] = 0;
        diffRgb[oi + 2] = 0;
      } else {
        // dimmed grayscale passthrough so the diff image still shows context
        const gray = Math.round((a.pixels[ai] + a.pixels[ai + 1] + a.pixels[ai + 2]) / 3 / 2) + 64;
        diffRgb[oi] = gray;
        diffRgb[oi + 1] = gray;
        diffRgb[oi + 2] = gray;
      }
    }
  }
  const totalPixels = width * height;
  return {
    width,
    height,
    totalPixels,
    diffCount,
    diffPercent: (diffCount / totalPixels) * 100,
    diffRgb,
  };
}

/** Crop a decoded image to [x, y, w, h] (all in source pixels). */
export function cropDecoded(img, x, y, w, h) {
  const out = new Uint8Array(w * h * img.channels);
  for (let row = 0; row < h; row++) {
    const srcStart = ((y + row) * img.width + x) * img.channels;
    const destStart = row * w * img.channels;
    out.set(img.pixels.subarray(srcStart, srcStart + w * img.channels), destStart);
  }
  return { width: w, height: h, channels: img.channels, pixels: out };
}

export function diffFiles(pathA, pathB, outPath, opts = {}) {
  let a = decodePNG(readFileSync(pathA));
  let b = decodePNG(readFileSync(pathB));
  if (opts.crop) {
    const [x, y, w, h] = opts.crop;
    a = cropDecoded(a, x, y, w, h);
    b = cropDecoded(b, x, y, w, h);
  }
  const result = diffDecoded(a, b, opts);
  if (outPath) {
    writeFileSync(outPath, encodePNG(result.width, result.height, result.diffRgb));
  }
  return { totalPixels: result.totalPixels, diffCount: result.diffCount, diffPercent: result.diffPercent };
}

// CLI entry point
if (import.meta.url === `file://${process.argv[1]}`) {
  const args = process.argv.slice(2).filter((a) => !a.startsWith('--'));
  const cropArg = process.argv.find((a) => a.startsWith('--crop='));
  const [a, b, out] = args;
  if (!a || !b) {
    console.error('usage: pixel-diff.mjs <a.png> <b.png> [out-diff.png] [--crop=x,y,w,h]');
    process.exit(1);
  }
  const opts = cropArg ? { crop: cropArg.slice('--crop='.length).split(',').map(Number) } : {};
  try {
    const result = diffFiles(a, b, out, opts);
    console.log(JSON.stringify(result, null, 2));
  } catch (err) {
    console.log(JSON.stringify({ error: String(err.message || err) }, null, 2));
  }
}
