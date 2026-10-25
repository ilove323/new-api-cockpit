/* Account tools: placeholder MCP examples only; no model or credential requests. */
(() => {
  'use strict';
  if (!document.getElementById('auxiliary-tools-page')) return;
  const $ = id => document.getElementById(`manual-${id}`);
  const module = window.CockpitManualGuides;
  const site = document.getElementById('tools-site-origin');
  if (site) site.textContent = location.origin;
  // Account-MCP examples are independent of the model-token selection. They
  // contain placeholders only: never call /manual/key for a user PAT.
  function mcpPreview() {
    if (typeof module?.buildMcp !== 'function' || !$('mcp-code')) return;
    try {
      const snippet = module.buildMcp($('mcp-client').value || 'codex', location.origin);
      $('mcp-url').textContent = new URL('/cockpit/mcp', location.origin).href;
      $('mcp-code-title').textContent = snippet.title;
      $('mcp-code').textContent = snippet.content;
      $('mcp-copy-status').textContent = '';
    } catch { $('mcp-code').textContent = '无法生成 MCP 示例，请核对站点地址。'; }
  }
  $('mcp-client')?.addEventListener('change', mcpPreview);
  $('mcp-copy')?.addEventListener('click', async () => {
    try {
      const snippet = module.buildMcp($('mcp-client').value || 'codex', location.origin);
      await navigator.clipboard.writeText(snippet.content);
      $('mcp-copy-status').textContent = '已复制。请在客户端单独配置用户 API PAT；本页不会读取或填入它。';
    } catch { $('mcp-copy-status').textContent = '复制被阻止，请手动复制占位符配置。'; }
  });
  mcpPreview();
})();
