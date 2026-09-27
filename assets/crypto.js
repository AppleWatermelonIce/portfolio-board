/* ============================================================================
 * crypto.js —— 客户端解密层（方案 A：口令保护静态站数据）
 *
 * 职责：用访问口令派生密钥，解密部署时生成的 assets/data.js.enc。
 *   · 仅做本地解密，不发起任何外部网络请求。
 *   · 与部署脚本 tools/encrypt_site.mjs（Node webcrypto）使用完全相同的
 *     PBKDF2-SHA256 + AES-GCM-256 参数，二者完全互通。
 *   · 口令从不上行；AES-GCM 自带完整性校验，口令错误会解密失败（不泄露任何明文）。
 * ========================================================================== */
(function () {
  'use strict';
  const enc = new TextEncoder();
  const dec = new TextDecoder();

  function hexToBytes(h) {
    const b = new Uint8Array(h.length / 2);
    for (let i = 0; i < b.length; i++) b[i] = parseInt(h.substr(i * 2, 2), 16);
    return b;
  }
  function b64ToBytes(b64) {
    const bin = atob(b64);
    const b = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) b[i] = bin.charCodeAt(i);
    return b;
  }
  function bytesToB64(bytes) {
    let bin = '';
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }

  // 口令 → AES-GCM 密钥（PBKDF2-SHA256，salt 取自卫公开 vault.json）
  async function deriveKey(pass, saltHex, iter) {
    const salt = hexToBytes(saltHex);
    const base = await crypto.subtle.importKey('raw', enc.encode(pass), 'PBKDF2', false, ['deriveKey']);
    return crypto.subtle.deriveKey(
      { name: 'PBKDF2', salt, iterations: iter, hash: 'SHA-256' },
      base,
      { name: 'AES-GCM', length: 256 },
      false,
      ['decrypt', 'encrypt']
    );
  }

  // b64 = base64( IV(12字节) || 密文 )
  async function decrypt(b64, key) {
    const buf = b64ToBytes(b64);
    const iv = buf.slice(0, 12);
    const ct = buf.slice(12);
    const pt = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, ct);
    return dec.decode(pt);
  }

  async function encrypt(text, key) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, enc.encode(text));
    const merged = new Uint8Array(12 + ct.byteLength);
    merged.set(iv, 0);
    merged.set(new Uint8Array(ct), 12);
    return bytesToB64(merged);
  }

  window.AppCrypto = { deriveKey, decrypt, encrypt, _b64ToBytes: b64ToBytes, _bytesToB64: bytesToB64 };
})();
