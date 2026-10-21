(function (root, factory) {
  'use strict';
  const value = factory(typeof module === 'object' && module.exports ? require('./state.js') : root.CockpitChat);
  if (typeof module === 'object' && module.exports) module.exports = value;
  else Object.assign(root.CockpitChat, value);
})(typeof window === 'object' ? window : globalThis, function (C) {
  'use strict';
  function fileSizeLabel(bytes) {
    if (!Number.isSafeInteger(bytes) || bytes < 0) return '';
    return bytes >= 1048576 ? `${Number((bytes / 1048576).toFixed(2))} MB` : bytes >= 1024 ? `${Number((bytes / 1024).toFixed(2))} KB` : `${bytes} 字节`;
  }
  function checkUpload(file, config) {
    if (!file || !config) throw new Error('请先读取文件限制，再选择一个文件。');
    const extension = /\.[a-z0-9]+$/i.exec(file.name)?.[0].toLowerCase();
    if (!config.allowed_file_extensions.includes(extension)) throw new Error('仅支持允许列表中的 UTF-8 文本；不支持图片、Office、PDF、压缩包。');
    if (file.size > config.limits.upload_file_bytes) throw new Error(`单文件不能超过 ${fileSizeLabel(config.limits.upload_file_bytes)}。`);
  }
  function safeFilename(name) { return String(name || 'download.txt').replace(/[\u0000-\u001f\u007f/\\:]/g, '_').slice(0, 180) || 'download.txt'; }
  async function readSkillFile(file, limit) {
    if (!file || file.name !== 'SKILL.md' || file.size > limit) throw new Error('请选择单个、文件名恰为 SKILL.md 且未超限的 UTF-8 文本。');
    // Keep the BOM here; parseSkill normalizes it exactly once, not twice.
    return new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(await file.arrayBuffer());
  }
  async function downloadFile({api, scope, cid, file, doc, ticket = scope.ticket(), urlAPI = URL, schedule = setTimeout}) {
    const blob = await api.download(cid, file.id, {ticket}); scope.assert(ticket);
    const url = scope.addURL(urlAPI.createObjectURL(blob), ticket);
    const a = doc.createElement('a'); a.href = url; a.download = safeFilename(file.name); a.rel = 'noopener'; doc.body.append(a);
    try { a.click(); } finally { a.remove(); schedule(() => scope.dropURL(url), 30000); }
  }
  return {checkUpload, fileSizeLabel, safeFilename, readSkillFile, downloadFile};
});
