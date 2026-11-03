// 参考实现导出器：用 JS 版 descramble 生成原始 RGBA，供 Python 版做逐像素对照。
// 用法: node ref_descramble.mjs <w> <h> <pk> <out.raw>
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const [w, h, pk, out] = process.argv.slice(2);
const W = Number(w);
const H = Number(h);
const PK = Number(pk);

const libDir = path.resolve(import.meta.dirname, '..', 'app', 'extlib');
const LIB = pathToFileURL(libDir + path.sep).href;
const { descrambleRgba } = await import(LIB + 'descramble.js');

// 确定性伪随机输入，保证 Python 侧能生成完全一样的数据
const src = new Uint8Array(W * H * 4);
let s = 123456789;
for (let i = 0; i < src.length; i++) {
  s = (s * 1664525 + 1013904223) >>> 0;
  src[i] = s & 0xff;
}

const res = descrambleRgba(src, W, H, PK, true);
fs.writeFileSync(out, Buffer.from(res.buffer, res.byteOffset, res.byteLength));
console.log(`wrote ${out} ${res.length} bytes (${W}x${H} pk=${PK})`);
