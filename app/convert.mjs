// Sketchfab 模型转换器：把 CLI 解密好的 osgjs 转成带材质贴图的 GLB
// 用法: node convert.mjs <uid> <素材目录> <输出.glb> [extlib目录]
//
// v1.1.8：贴图改为**单管线**——Python 引擎已经下载过一遍（textures/ 与
// textures_manifest.json），本脚本只读本地文件并对带 pk 的图做解密回写，
// 不再自己联网重复下载（SFD-005）。只有目录里没有 manifest 时，才回退到
// 旧行为（自己抓一遍），保证对历史目录仍然可用。
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
const { descrambleImageBytes } = await import(LIB + 'descramble.js');
const { convertOsgjsToGlbFromFiles } = await import(LIB + 'osgjs2gltf.js');

// Python 引擎把清单写在 textures/ 里（info.json 的 texture_manifest 字段也指向这里），
// 早期版本写在模型根目录。两处都探一遍——只认一处会让转换器误判"没有本地贴图"，
// 于是又联网抓一遍，SFD-005 就白修了。
function findManifest() {
  const candidates = [
    path.join(dir, 'textures', 'textures_manifest.json'),
    path.join(dir, 'textures_manifest.json'),
  ];
  for (const p of candidates) if (fs.existsSync(p)) return p;
  return null;
}
const MANIFEST = findManifest();

function isPow2(n) { return n > 0 && (n & (n - 1)) === 0; }
function descramblable(entry) {
  if (entry.pk == null) return true;                 // 未加密，无需解密
  const w = entry.width || 0;
  const h = entry.height || 0;
  if (!w || !h) return true;
  return isPow2(w) && isPow2(h);                     // 非 2 的幂解出来是错位拼贴
}

/**
 * 从本地 textures/ + manifest 构造贴图列表（不联网）。
 * 带 pk 的图在这里解密，并把解密结果**写回磁盘**，保证散装贴图与 GLB 内嵌一致。
 */
async function texturesFromDisk(log) {
  const manifest = JSON.parse(fs.readFileSync(MANIFEST, 'utf8'));
  const list = [];
  const warnings = [];
  const entries = manifest.textures || [];
  for (const e of entries) {
    const file = path.join(dir, 'textures', e.file);
    if (!fs.existsSync(file)) {
      warnings.push({ file: e.file, reason: 'missing-on-disk' });
      continue;
    }
    let data = new Uint8Array(fs.readFileSync(file));
    let outName = e.file;
    let fromDescramble = false;
    if (e.pk != null && descramblable(e)) {
      try {
        const dec = await descrambleImageBytes(data, e.pk, (m) => log('      ' + m));
        if (dec && dec.length > 64) {
          data = new Uint8Array(dec);
          fromDescramble = true;
          // 解密结果一律是 PNG：原扩展名不是 .png 时改名，避免 mime 与内容不符
          if (!/\.png$/i.test(outName)) {
            const newName = outName.replace(/\.[^.]+$/, '') + '.png';
            const newPath = path.join(dir, 'textures', newName);
            fs.writeFileSync(newPath, Buffer.from(data));
            try { fs.unlinkSync(file); } catch (_) {}
            outName = newName;
            e.file = newName;
          } else {
            fs.writeFileSync(file, Buffer.from(data));
          }
        } else {
          warnings.push({ file: e.file, reason: 'descramble-empty' });
        }
      } catch (err) {
        warnings.push({ file: e.file, reason: 'descramble-failed: ' + (err && err.message ? err.message : err) });
      }
    } else if (e.pk != null) {
      warnings.push({ file: e.file, reason: 'not-power-of-two' });
    }
    e.descrambled = fromDescramble;
    list.push({
      name: 'textures/' + outName,
      data,
      uid: e.uid,
      imageUids: e.image_uids || [],
      fileNames: e.file_names || [],
      originalName: outName,
      width: e.width || 0,
      height: e.height || 0,
      fromDescramble,
      pk: e.pk,
    });
  }
  // 回写 manifest：记录解密状态与实际文件名，便于用户/后续脚本核对
  manifest.textures = entries;
  manifest.descrambled = entries.filter((e) => e.descrambled).length;
  manifest.warnings = warnings;
  fs.writeFileSync(MANIFEST, JSON.stringify(manifest, null, 2));
  return { list, warnings, via: 'manifest' };
}

console.log('[1/4] 读取模型信息…');
const info = await fetchViewerInfo(uid);
const vm = parseViewerMaterials(info);
console.log(`      材质规格 ${vm ? vm.size : 0} 项`);

console.log('[2/4] 使用本地贴图（不重复下载）…');
let textures = [];
let texWarnings = [];
let texSource = 'none';
if (MANIFEST) {
  const got = await texturesFromDisk((m) => console.log(m));
  textures = got.list;
  texWarnings = got.warnings;
  texSource = got.via;
  console.log(`      清单 ${path.relative(dir, MANIFEST) || MANIFEST}`);
  console.log(`      贴图 ${textures.length} 张（本地，解密 ${textures.filter((t) => t.fromDescramble).length} 张）`);
} else {
  // 兼容历史目录：没有 manifest 时回退旧行为（会联网一次）
  console.log('      未找到 textures_manifest.json，回退为重新抓取贴图…');
  textures = await fetchTextures(uid, (m) => console.log('      ' + m), { concurrency: 2 });
  texSource = 'network-fallback';
  console.log(`      贴图 ${textures.length} 张（联网）`);
}

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

// 转换报告：GUI / info.json 据此向用户提示（SFD-011）
const animCount = Number(info.animationCount || 0) || 0;
const report = {
  schema: 1,
  uid,
  glb: path.basename(outFile),
  meshes: res.geometryCount,
  images: res.textureEmbedCount,
  textures_local: textures.length,
  textures_descrambled: textures.filter((t) => t.fromDescramble).length,
  texture_source: texSource,
  texture_warnings: texWarnings,
  animation_count: animCount,
  animations_embedded: 0,
};
fs.writeFileSync(path.join(dir, 'convert_report.json'), JSON.stringify(report, null, 2));

for (const w of texWarnings) console.log(`      WARN texture ${w.file}: ${w.reason}`);
if (animCount > 0) {
  console.log(`      WARN 该模型含 ${animCount} 段动画，导出为静态几何（不含动画/骨骼）`);
}
console.log(`DONE ${(res.glb.length / 1024 / 1024).toFixed(1)}MB meshes=${res.geometryCount} images=${res.textureEmbedCount}` +
  (texWarnings.length ? ` textureWarnings=${texWarnings.length}` : ''));
