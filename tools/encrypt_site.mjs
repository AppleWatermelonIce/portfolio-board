// ============================================================================
// encrypt_site.mjs —— 部署时加密本地敏感数据
//
// 读取环境变量 SITE_PASSWORD，把 public/ 下的持仓明文转成 .enc 密文：
//   · assets/data.js        （window.PORTFOLIO_DATA，含全部持仓 + 净值序列）
//   · data/products.json    （标的清单，真相源）
//   · data/data.json        （data.js 的同内容降级种子）
//   · data/history/*.json   （每标的全序列本地缓存）
// 同时把原明文路径改写为非敏感占位（避免 404 / 误暴露），并写入公开 vault.json
// （仅含 salt + 迭代次数，不含任何密钥）。
//
// 与浏览器 assets/crypto.js 参数完全一致（PBKDF2-SHA256 + AES-GCM-256）。
// 未设置 SITE_PASSWORD 时直接退出(1)，拒绝以明文部署。
// ============================================================================
import { webcrypto } from 'node:crypto';
import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs';
import { join, basename } from 'node:path';

const ROOT = process.argv[2] || 'public';
const PASS = process.env.SITE_PASSWORD;
if (!PASS) {
  console.error('❌ 未设置 SITE_PASSWORD：拒绝以明文部署。请在仓库 Settings → Secrets 添加 SITE_PASSWORD 后重试。');
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
async function encryptText(text, key) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, enc.encode(text));
  const merged = new Uint8Array(12 + ct.byteLength);
  merged.set(iv, 0);
  merged.set(new Uint8Array(ct), 12);
  return b64(merged);
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
  writeFileSync(join(ROOT, 'vault.json'), JSON.stringify(vault, null, 2) + '\n');

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

  let n = 0;
  for (const f of targets) {
    if (!existsSync(f)) continue;
    const text = readFileSync(f, 'utf8');
    const ct = await encryptText(text, key);
    writeFileSync(f + '.enc', ct);
    writeFileSync(f, stubFor(f)); // 明文路径改写为非敏感占位
    n++;
  }
  console.log(`✅ 已加密 ${n} 个文件 → .enc；vault.json 已写入 ${ROOT}（salt=${vault.salt}）`);
}

main().catch((e) => {
  console.error('加密失败:', e);
  process.exit(1);
});
