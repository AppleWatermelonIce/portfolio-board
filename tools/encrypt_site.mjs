// ============================================================================
// encrypt_site.mjs —— 部署时加密本地敏感数据
//
// 读取环境变量 SITE_PASSWORD，把 public/ 下的持仓明文转成密文：
//   · assets/data.js        （window.PORTFOLIO_DATA，含全部持仓 + 净值序列）
//                           → 紧凑格式 gz-bin-v1：gzip + AES-GCM + 裸二进制分片
//                             （网络传输 2.95MB → 约 570KB，手机弱网可正常拉取）
//   · data/products.json    （标的清单，真相源）        → 旧 base64 单文件 .enc
//   · data/data.json        （data.js 的同内容降级种子） → 旧 base64 单文件 .enc
//   · data/history/*.json   （每标的全序列本地缓存）     → 旧 base64 单文件 .enc
// 同时把原明文路径改写为非敏感占位（避免 404 / 误暴露），并写入公开 vault.json
// （仅含 salt + 迭代次数 + 格式/分片数/总字节/签名，不含任何密钥）。
//
// 与浏览器 assets/crypto.js 参数完全一致（PBKDF2-SHA256 + AES-GCM-256）。
// 未设置 SITE_PASSWORD 时直接退出(1)，拒绝以明文部署。
// ============================================================================
import { webcrypto, createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { join, basename } from 'node:path';

// 主载荷（assets/data.js，登录关键路径）采用「gzip → AES-GCM → 裸二进制分片」紧凑格式：
//   · gzip 把 2.11MB 明文压到约 555KB（约 3.9x）
//   · 裸二进制省掉 base64 的 33% 膨胀，并把 2.95MB 网络传输降到约 555KB（合计约 5.3x）
//   · 分片便于移动端并行下载 + 单片重试（弱网下不必整包重来）
// 其余文件（products.json / data.json / history/*.json）不在登录关键路径，沿用旧 base64 单文件格式。
const CHUNK = 262144; // 单片目标 256KB

const ROOT = process.argv[2] || 'public';
const PASS = process.env.SITE_PASSWORD;
if (!existsSync(ROOT)) {
  console.error(`::error::ENC_NOROOT 待加密目录不存在：${ROOT}（cwd=${process.cwd()}）`);
  process.exit(1);
}
if (!PASS) {
  console.error('::error::ENC_NOPASS 未读到口令（SITE_PASSWORD 为空）：拒绝以明文部署。');
  console.error('   请到 仓库 Settings → Secrets and variables → Actions → "New repository secret"');
  console.error('   密钥名必须一字不差为 SITE_PASSWORD，且须是「Repository secrets」仓库级密钥。');
  process.exit(1);
}

const crypto = webcrypto;
const enc = new TextEncoder();
const b64 = (buf) => Buffer.from(buf).toString('base64');

async function deriveKey(pass, salt, iter) {
  const base = await crypto.subtle.importKey('raw', enc.encode(pass), 'PBKDF2', false, ['deriveKey']);
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt, iterations: iter, hash: 'SHA-256' },
    base,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt']
  );
}
async function encryptBytes(bytes, key) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, bytes);
  const merged = new Uint8Array(12 + ct.byteLength);
  merged.set(iv, 0);
  merged.set(new Uint8Array(ct), 12);
  return merged;
}
async function encryptText(text, key) {
  return b64(await encryptBytes(enc.encode(text), key));
}
function stubFor(file) {
  if (file.endsWith('.js')) return `/* 本文件已加密，密文见 ${basename(file)}.enc */\n`;
  return JSON.stringify({ __encrypted: true, note: '敏感数据已加密，密文见同目录 .enc 文件' }) + '\n';
}

async function main() {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iter = 150000;
  const key = await deriveKey(PASS, salt, iter);
  const vault = {
    v: 1,
    algo: 'AES-GCM-256',
    kdf: 'PBKDF2-SHA256',
    iter,
    salt: Buffer.from(salt).toString('hex'),
  };

  const targets = [
    join(ROOT, 'assets', 'data.js'),
    join(ROOT, 'data', 'products.json'),
    join(ROOT, 'data', 'data.json'),
  ];
  const histDir = join(ROOT, 'data', 'history');
  if (existsSync(histDir)) {
    for (const f of readdirSync(histDir)) {
      if (f.endsWith('.json')) targets.push(join(histDir, f));
    }
  }

  let n = 0, mainInfo = '';
  for (const f of targets) {
    if (!existsSync(f)) continue;
    const raw = readFileSync(f); // Buffer（主载荷走二进制，其余按 utf8 文本处理）

    // —— 主载荷：gzip + AES-GCM + 裸二进制分片 ——
    if (basename(f) === 'data.js') {
      const gz = gzipSync(raw, { level: 9 });
      const blob = await encryptBytes(new Uint8Array(gz), key);
      const parts = Math.max(1, Math.ceil(blob.length / CHUNK));
      for (let i = 0; i < parts; i++) {
        const seg = blob.subarray(i * CHUNK, Math.min(blob.length, (i + 1) * CHUNK));
        const out = parts > 1 ? `${f}.enc.bin.${i}` : `${f}.enc.bin`;
        writeFileSync(out, Buffer.from(seg));
      }
      vault.fmt = 'gz-bin-v1';
      vault.parts = parts;
      vault.bytes = blob.length;
      vault.sig = createHash('sha256').update(blob).digest('hex').slice(0, 16);
      writeFileSync(f, stubFor(f)); // 明文路径改写为非敏感占位
      n++;
      mainInfo = `主载荷 ${(raw.length / 1048576).toFixed(2)}MB → gzip ${(gz.length / 1024).toFixed(0)}KB → 密文 ${(blob.length / 1024).toFixed(0)}KB/${parts}片（sig=${vault.sig}）`;
      continue;
    }

    // —— 其余文件：沿用旧 base64 单文件格式（不在登录关键路径）——
    const ct = await encryptText(raw.toString('utf8'), key);
    writeFileSync(f + '.enc', ct);
    writeFileSync(f, stubFor(f));
    n++;
  }

  writeFileSync(join(ROOT, 'vault.json'), JSON.stringify(vault, null, 2) + '\n');
  console.log(`✅ 已加密 ${n} 个文件；vault.json 已写入 ${ROOT}（salt=${vault.salt}）`);
  if (mainInfo) console.log(`   ${mainInfo}`);
}

main().catch((e) => {
  console.error('::error::ENC_FAIL ' + (e && e.message ? e.message : String(e)));
  process.exit(1);
});
