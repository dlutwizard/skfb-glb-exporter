// Sketchfab 模型转换器：把 CLI 解密好的 osgjs 转成带材质贴图的 GLB
// 用法: node convert.mjs <uid> <素材目录> <输出.glb> [extlib目录]
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import sharp from 'sharp';

const uid = process.argv[2];
const dir = process.argv[3];
const outFile = process.argv[4];
const libDir = process.argv[5] || path.join(import.meta.dirname, 'extlib');

if (!uid || !dir || !outFile) {
  console.error('用法: node convert.mjs <uid> <目录> <输出.glb> [extlib目录]');
  process.exit(1);
}

const LIB = pathToFileURL(libDir.endsWith(path.sep) ? libDir : libDir + path.sep).href;

// ---- 浏览器 API 垫片：给 descramble 模块提供 JPEG 解码 ----
globalThis.createImageBitmap = async (blob) => {
  const buf = Buffer.from(await blob.arrayBuffer());
  const { data, info } = await sharp(buf).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  return {
    width: info.width,
    height: info.height,
    _rgba: new Uint8ClampedArray(data),
    close() {},
  };
};

globalThis.OffscreenCanvas = class {
  constructor(w, h) {
    this.width = w;
    this.height = h;
    this._bmp = null;
  }
  getContext() {
    const self = this;
    return {
      drawImage(bmp) { self._bmp = bmp; },
      getImageData(x, y, w, h) {
        const src = self._bmp?._rgba || new Uint8ClampedArray(w * h * 4);
        return { data: new Uint8ClampedArray(src), width: w, height: h };
      },
    };
  }
};

const { fetchViewerInfo, parseViewerMaterials, fetchTextures, viewerOrientation } =
  await import(LIB + 'sf-api.js');
const { convertOsgjsToGlbFromFiles } = await import(LIB + 'osgjs2gltf.js');

console.log('[1/4] 读取模型信息…');
const info = await fetchViewerInfo(uid);
const vm = parseViewerMaterials(info);
console.log(`      材质规格 ${vm ? vm.size : 0} 项`);

console.log('[2/4] 下载并解密贴图…');
const textures = await fetchTextures(uid, (m) => console.log('      ' + m), { concurrency: 2 });
console.log(`      贴图 ${textures.length} 张`);

console.log('[3/4] 读取本地网格数据…');
const files = {};
for (const f of ['file.osgjs', 'model_file.bin', 'model_file_wireframe.bin']) {
  const p = path.join(dir, f);
  if (fs.existsSync(p)) {
    files[f] = new Uint8Array(fs.readFileSync(p));
  }
}

console.log('[4/4] 转换 osgjs → GLB…');
const res = await convertOsgjsToGlbFromFiles(files, vm, textures, viewerOrientation(info));
fs.writeFileSync(outFile, Buffer.from(res.glb));
console.log(`DONE ${(res.glb.length / 1024 / 1024).toFixed(1)}MB meshes=${res.geometryCount} images=${res.textureEmbedCount}`);
