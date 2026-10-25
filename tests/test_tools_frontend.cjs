'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('src/new_api_cockpit/static/tools.js', 'utf8');
const manual = fs.readFileSync('src/new_api_cockpit/static/manual.js', 'utf8');
const {buildMcp} = require('../src/new_api_cockpit/static/manual-guides.js');

test('account-tool examples preserve the site port and never query model keys or account data', async () => {
  const elements = new Map(), writes = [];
  for (const id of ['auxiliary-tools-page', 'tools-site-origin', 'manual-mcp-url', 'manual-mcp-client', 'manual-mcp-code', 'manual-mcp-code-title', 'manual-mcp-copy', 'manual-mcp-copy-status']) {
    elements.set(id, {value: '', textContent: '', events: {}, addEventListener(name, action) {this.events[name] = action;}});
  }
  const context = {document: {getElementById: id => elements.get(id)}, URL, location: {origin: 'https://example.test:24444'},
    navigator: {clipboard: {async writeText(text) {writes.push(text);}}},
    fetch() {assert.fail('auxiliary tool documentation must not fetch credentials or model lists');}, CockpitManualGuides: {buildMcp}};
  context.window = context; vm.runInNewContext(source, context);
  assert.equal(elements.get('tools-site-origin').textContent, 'https://example.test:24444');
  assert.equal(elements.get('manual-mcp-url').textContent, 'https://example.test:24444/cockpit/mcp');
  await elements.get('manual-mcp-copy').events.click();
  assert(writes[0].includes('NEW_API_USER_PAT')); assert(!writes[0].includes('OPENAI_API_KEY'));
  elements.get('manual-mcp-client').value = 'cursor'; elements.get('manual-mcp-client').events.change();
  await elements.get('manual-mcp-copy').events.click();
  assert.equal(JSON.parse(writes[1]).mcpServers.newapi_account.headers.Authorization, 'Bearer USER_API_TOKEN');
});
test('old MCP bookmarks redirect before model-token initialization', () => {
  let destination;
  const context = {document: {getElementById: () => ({})}, location: {hash: '#manual-mcp-title', replace(path) {destination = path;}}};
  vm.runInNewContext(manual, context);
  assert.equal(destination, '/cockpit/tools#manual-mcp-title');
});
