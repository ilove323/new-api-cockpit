'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {parse: json} = require('jsonc-parser');
const {parseTOML, getStaticTOMLValue} = require('toml-eslint-parser');
const {claudeConfig, codexConfig, codexAuth} = require('../dist/client-config');
const KEY = 'sk-fixture_model_secret_not_real_123456', SITE = 'https://gateway.example.com:8443/cockpit/mcp';
const toml = text => getStaticTOMLValue(parseTOML(text, {tomlVersion: '1.0'}));

test('Claude replaces connection variables, preserves unrelated settings/comments, and is idempotent', () => {
  const old = `{
  // keep this setting and comment
  "editor.fontSize": 17,
  "claudeCode.environmentVariables": [
    {"name":"ANTHROPIC_BASE_URL","value":"https://old.example.com"},
    {"name":"ANTHROPIC_AUTH_TOKEN","value":"old-token"},
    {"name":"ANTHROPIC_AUTH_TOKEN","value":"duplicate-old-token"},
    {"name":"ANTHROPIC_API_KEY","value":"conflicting-key"},
    {"name":"CLAUDE_CODE_USE_BEDROCK","value":"1"},
    {"name":"HTTPS_PROXY","value":"http://localhost:7890"}
  ],
  "claudeCode.initialPermissionMode": "plan",
}`;
  const output = claudeConfig(old, SITE, KEY), parsed = json(output);
  const env = parsed['claudeCode.environmentVariables'];
  assert(output.includes('// keep this setting and comment'));
  assert.equal(parsed['editor.fontSize'], 17); assert.equal(parsed['claudeCode.initialPermissionMode'], 'plan');
  assert.equal(parsed['claudeCode.disableLoginPrompt'], true);
  assert.equal(env.find(v => v.name === 'ANTHROPIC_BASE_URL').value, 'https://gateway.example.com:8443');
  assert.equal(env.find(v => v.name === 'ANTHROPIC_AUTH_TOKEN').value, KEY);
  assert.equal(env.find(v => v.name === 'ANTHROPIC_API_KEY').value, '');
  assert.equal(env.find(v => v.name === 'HTTPS_PROXY').value, 'http://localhost:7890');
  for (const name of ['ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'CLAUDE_CODE_USE_BEDROCK']) assert.equal(env.filter(v => v.name === name).length, 1);
  assert.equal(claudeConfig(output, SITE, KEY), output);
  assert(!output.includes('old-token')); assert(!output.includes('conflicting-key'));
});
test('Codex overwrites only its provider branch and connection fields, preserving original model, MCP and comments', () => {
  const keep = '# unrelated comment\n[mcp_servers.keep]\ncommand = "npx"\nargs = ["-y", "package"]\n';
  const old = '# root comment\nmodel = "keep-selected-model"\nmodel_provider = "old" # keep inline\ncli_auth_credentials_store = "keyring"\nforced_login_method = "chatgpt"\n'
    + 'model_context_window = 272000\n[model_providers.newapi]\nname="old"\nenv_key="OLD_API_KEY"\n'
    + '[model_providers.newapi.auth]\ncommand="old-auth-command"\n[model_providers.newapi.http_headers]\nAuthorization="old-header"\n'
    + '[model_providers.other]\nbase_url="https://other.example.com"\n' + keep;
  const output = codexConfig(old, SITE), parsed = toml(output);
  assert.equal(parsed.model_provider, 'newapi'); assert.equal(parsed.cli_auth_credentials_store, 'file');
  assert.equal(parsed.forced_login_method, 'api'); assert.equal(parsed.model, 'keep-selected-model');
  assert.equal(parsed.model_context_window, 272000); assert(output.includes(keep)); assert(output.includes('# keep inline'));
  assert.equal(parsed.model_providers.other.base_url, 'https://other.example.com');
  assert.deepEqual(parsed.model_providers.newapi, {name: 'New API', base_url: 'https://gateway.example.com:8443/v1', wire_api: 'responses', requires_openai_auth: true});
  assert(!output.includes('old-auth-command')); assert(!output.includes('old-header')); assert(!output.includes('OLD_API_KEY'));
  assert.equal(codexConfig(output, SITE), output);
  assert(!output.includes(KEY));
});
test('Codex handles quoted keys, dotted keys, inline providers, multiline strings and CRLF without duplicating providers', () => {
  const forms = [
    'model_providers = {newapi = {name = "old", env_key="OLD"}, other={name="keep"}}\n',
    'model_providers = { other = {name="keep"} }\n',
    '[model_providers]\nnewapi = {name="old"}\nother = {name="keep"}\n',
    'model_providers.newapi.name = "old"\nmodel_providers.other.name = "keep"\n',
    '["model_providers"."newapi"]\nname = "old"\n[model_providers.other]\nname="keep"\n',
    'notice = """\n[model_providers.newapi]\nnot a real table\n"""\n[model_providers.other]\nname="keep"\n'
  ];
  for (const old of forms) {
    const output = codexConfig(old.replace(/\n/g, '\r\n'), SITE), parsed = toml(output);
    assert.equal(parsed.model_providers.newapi.base_url, 'https://gateway.example.com:8443/v1');
    assert.equal(parsed.model_providers.other.name, 'keep');
    assert.equal(codexConfig(output, SITE), output); assert(!/(?<!\r)\n/.test(output));
  }
});
test('Codex auth replaces old OAuth state and does not mix keys from different login modes', () => {
  const output = codexAuth('{"tokens":{"refresh_token":"fixture-old"},"last_refresh":"old","OPENAI_API_KEY":"old"}', KEY);
  assert.deepEqual(JSON.parse(output), {auth_mode: 'apikey', OPENAI_API_KEY: KEY});
  assert.equal(codexAuth(output, KEY), output); assert(!output.includes('fixture-old'));
});
test('new configs are valid; malformed configs, duplicate fields and unsafe input do not produce partial output', () => {
  assert.equal(toml(codexConfig('', SITE)).model_provider, 'newapi');
  assert.equal(json(claudeConfig('', SITE, KEY))['claudeCode.environmentVariables'].find(v => v.name === 'ANTHROPIC_AUTH_TOKEN').value, KEY);
  for (const old of ['{invalid', '{"claudeCode.environmentVariables": {}}', '{"editor.fontSize":1,"editor.fontSize":2}']) assert.throws(() => claudeConfig(old, SITE, KEY));
  for (const old of ['model="one"\nmodel="two"', '[broken', 'model_providers = false']) assert.throws(() => codexConfig(old, SITE));
  assert.throws(() => codexAuth('{broken', KEY));
  assert.throws(() => claudeConfig('{}', SITE, 'nap_user_pat_not_a_model_key'));
  assert.throws(() => codexConfig('', 'https://user:password@example.com'));
});
