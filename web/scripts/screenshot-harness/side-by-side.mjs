/**
 * Compose two same-height PNGs side by side with a thin divider — for the
 * THR-105 Todos fidelity evidence bundle (TASK-3834). Evidence-only tooling.
 * Usage: node side-by-side.mjs <left.png> <right.png> <out.png>
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { decodePNG, encodePNG } from './pixel-diff.mjs';

const [leftPath, rightPath, outPath] = process.argv.slice(2);
const left = decodePNG(readFileSync(leftPath));
const right = decodePNG(readFileSync(rightPath));
const h = Math.max(left.height, right.height);
const divider = 4;
const w = left.width + right.width + divider;
const out = new Uint8Array(w * h * 3).fill(200);

function blit(src, destX) {
  for (let y = 0; y < src.height; y++) {
    for (let x = 0; x < src.width; x++) {
      const si = (y * src.width + x) * src.channels;
      const di = (y * w + destX + x) * 3;
      out[di] = src.pixels[si];
      out[di + 1] = src.pixels[si + 1];
      out[di + 2] = src.pixels[si + 2];
    }
  }
}
blit(left, 0);
blit(right, left.width + divider);
writeFileSync(outPath, encodePNG(w, h, Buffer.from(out)));
console.log(`wrote ${outPath} (${w}x${h})`);
