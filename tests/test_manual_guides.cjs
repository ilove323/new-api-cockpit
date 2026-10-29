/* Local fixtures only: no client launch, credentials, network, or paid calls. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const {guides, resolve, build, buildMcp} = require('../src/new_api_cockpit/static/manual-guides.js');

const origin = 'https://fixture.test:24443';
const options = extras => ({origin, model: 'fixture-model', ...extras});
const content = (id, extras) => build(id, options(extras)).map(item => item.content).join('\n');
const jsonBlock = extras => JSON.parse(build('openclaw', options(extras))[0].content);
const titles = ['Claude Code', 'Codex', 'WorkBuddy', 'Hermes', 'Claude App', 'Cursor', 'CC Switch', 'TRAE', 'Qoder', 'Chatbox', 'OpenClaw', 'Pi Agent', 'DeepSeek Harness', 'CodeBuddy', 'curl'];

test('new clients use their documented paths and cannot confuse user PAT with model configuration', () => {
  const work = build('workbuddy', options())[1];
  assert.ok(work.content.includes(origin + '/v1/chat/completions'));
  assert.match(work.content, /自定义协议.*开启/);
  const hermes = build('hermes', options());
  assert.equal(hermes[0].language, 'yaml');
  assert.match(hermes[0].content, /transport: chat_completions/);
  assert.ok(hermes[0].content.includes(origin + '/v1'));
  assert.match(hermes[1].content, /^hermes --provider=newapi --model='fixture-model'$/);
  const desktop = build('claude-app', options())[0].content;
  assert.ok(desktop.includes('Gateway base URL：' + origin + '\n'));
  assert.match(desktop, /Gateway auth scheme：Bearer/);
  assert.ok(!desktop.includes('/v1/messages'));
  assert.ok(guides.every(item => item.steps.length >= 4));
});

test('all clients attach each example to its operation step on every platform and protocol', () => {
  const expected = {
    'claude-code': {2: [0], 3: [1]}, codex: {2: [0], 3: [1], 5: [2]},
    workbuddy: {2: [0], 3: [1]}, hermes: {2: [0], 4: [1]},
    'claude-app': {2: [0], 3: [1]}, cursor: {1: [0], 2: [1], 3: [2]},
    'cc-switch': {2: [0], 4: [1]}, trae: {2: [0]}, qoder: {2: [0], 3: [1]},
    chatbox: {1: [0], 2: [1], 3: [2]}, openclaw: {2: [0], 3: [1]}, 'pi-agent': {1: [0], 3: [1], 4: [2]}, 'deepseek-harness': {1: [0], 3: [1], 4: [2]}, codebuddy: {3: [0], 4: [1]}, curl: {3: [0]},
  };
  for (const guide of guides) {
    const steps = Object.fromEntries(guide.steps.flatMap((step, i) => step.snippets ? [[i + 1, step.snippets]] : []));
    assert.deepEqual(steps, expected[guide.id], guide.id);
    for (const protocol of guide.protocols) for (const platform of ['posix', 'powershell']) {
      for (const target of guide.id === 'cc-switch' ? ['claude-code', 'codex'] : [undefined]) {
        const blocks = build(guide.id, options({protocol, platform, target}));
        const indices = guide.steps.flatMap(step => step.snippets || []).sort((a, b) => a - b);
        assert.deepEqual(indices, blocks.map((_, i) => i), `${guide.id}/${protocol}/${platform}/${target}`);
        for (const block of blocks) {
          assert.equal(block.containsKey, block.content.includes('API_KEY'), `${guide.id}: only credential steps fetch KEY`);
        }
      }
    }
  }
});

test('Codex two-file setup has a nonsecret connection and independent auth.json', () => {
  for (const platform of ['posix', 'powershell']) {
    const result = build('codex', options({platform, key: 'copy-only-model-key'}));
    assert.equal(result[0].containsKey, false);
    assert.match(result[0].content, /cli_auth_credentials_store = "file"/);
    assert.match(result[0].content, /requires_openai_auth = true/);
    assert.doesNotMatch(result[0].content, /env_key|copy-only-model-key/);
    assert.equal(result[1].language, 'json');
    assert.deepEqual(JSON.parse(result[1].content), {auth_mode: 'apikey', OPENAI_API_KEY: 'copy-only-model-key'});
    assert.equal(result[1].containsKey, true);
    assert.equal(result[2].containsKey, false);
  }
});

test('MCP examples contain only user-PAT placeholders, need no model/key, and preserve ports', () => {
  const codex = buildMcp('codex', origin);
  assert.equal(codex.containsKey, false);
  assert.ok(codex.content.includes(origin + '/cockpit/mcp'));
  assert.match(codex.content, /bearer_token_env_var = "NEW_API_USER_PAT"/);
  assert.doesNotMatch(codex.content, /OPENAI_API_KEY|NEW_API_MODEL_KEY|sk-/);
  const cursor = JSON.parse(buildMcp('cursor', origin).content);
  assert.equal(cursor.mcpServers.newapi_account.url, origin + '/cockpit/mcp');
  assert.equal(cursor.mcpServers.newapi_account.headers.Authorization, 'Bearer USER_API_TOKEN');
  assert.throws(() => buildMcp('unknown', origin));
  assert.throws(() => buildMcp('codex', 'https://fixture.test/path'));
});

test('fifteen tools expose complete, immutable metadata and client protocols', () => {
  assert.deepEqual(guides.map(item => item.title), titles);
  assert.equal(new Set(guides.map(item => item.id)).size, 15);
  for (const item of guides) {
    assert.equal(item.status, undefined);
    assert.ok(item.description && item.steps.length && item.notes.length && item.sources.length);
    assert.ok(item.steps.every(step => typeof step.title === 'string' && typeof step.body === 'string'));
    assert.ok(!item.notes.some(note => /不代表.*(?:实测|测试通过)/.test(note)));
    assert.ok(!item.notes.some(note => note.includes('屏幕示例使用 API_KEY 占位')
      || note.includes('这里只生成文本，不保存配置')));
    assert.ok(item.sources.every(source => source.title && new URL(source.url).protocol === 'https:'));
    assert.ok(item.protocols.every(protocol => ['chat', 'responses', 'anthropic'].includes(protocol)));
    assert.equal(new Set(item.protocols).size, item.protocols.length);
    assert.ok(item.protocols.includes(item.defaultProtocol));
    assert.ok(Object.isFrozen(item) && Object.isFrozen(item.protocols) && Object.isFrozen(item.steps[0]));
  }
  assert.ok(Object.isFrozen(guides));
  assert.match(JSON.stringify(guides.find(item => item.id === 'cursor')), /Tab.*不走 BYOK/);
  assert.match(JSON.stringify(guides.find(item => item.id === 'trae')), /国际版/);
  assert.match(JSON.stringify(guides.find(item => item.id === 'codex')), /auth.json/);
  const codex = guides.find(item => item.id === 'codex');
  assert.equal(codex.steps[0].title, '备份并找到配置目录');
  assert.ok(codex.steps.some(step => step.title === '合并配置'));
  assert.ok(!JSON.stringify(codex.steps).includes('确认 Responses 支持'));
});

test('default previews use API_KEY and explicit-copy input never mutates or persists', () => {
  const before = JSON.stringify(guides);
  for (const item of guides) {
    const preview = build(item.id, options());
    assert.ok(preview.length && preview.every(part => part.title && part.language && typeof part.content === 'string'));
    assert.ok(preview.some(part => part.content.includes('API_KEY')));
    const copied = content(item.id, {key: 'fixture-copy-only-secret'});
    assert.ok(copied.includes('fixture-copy-only-secret'));
    assert.ok(!content(item.id).includes('fixture-copy-only-secret'));
  }
  assert.equal(JSON.stringify(guides), before);
});

test('origin joining preserves explicit ports, strips only root slash, and handles IPv6', () => {
  for (const value of [origin, origin + '/', 'https://fixture.test:443', 'http://fixture.test:80', 'http://localhost:3000', 'http://[::1]:4567/']) {
    const root = value.replace(/\/$/, '');
    assert.match(content('claude-code', {origin: value}), new RegExp(root.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    assert.ok(content('cursor', {origin: value}).includes(root + '/v1'));
    assert.ok(content('trae', {origin: value}).includes(root + '/v1/chat/completions'));
    assert.equal(jsonBlock({origin: value}).models.providers.newapi.baseUrl, root + '/v1');
  }
  assert.ok(content('cursor', {origin: 'HTTPS://FIXTURE.TEST:443/'}).includes('https://fixture.test:443/v1'));
});

test('protocol-specific endpoint, credential and OpenClaw adapters are exact', () => {
  const expected = {
    chat: {path: '/v1/chat/completions', api: 'openai-completions', base: origin + '/v1'},
    responses: {path: '/v1/responses', api: 'openai-responses', base: origin + '/v1'},
    anthropic: {path: '/v1/messages', api: 'anthropic-messages', base: origin},
  };
  for (const [protocol, value] of Object.entries(expected)) {
    const claw = jsonBlock({protocol});
    assert.equal(claw.models.providers.newapi.api, value.api);
    assert.equal(claw.models.providers.newapi.baseUrl, value.base);
    assert.equal(claw.models.providers.newapi.auth, 'api-key');
    assert.equal(claw.agents, undefined);
    assert.equal(claw.models.mode, undefined);
    const curl = content('curl', {protocol});
    assert.ok(curl.includes(origin + value.path));
    assert.ok(curl.includes(protocol === 'anthropic' ? 'x-api-key: API_KEY' : 'Authorization: Bearer API_KEY'));
    const body = JSON.parse(curl.split('--data-raw ')[1].slice(1, -1));
    assert.equal(body.model, 'fixture-model');
    if (protocol === 'anthropic') {
      assert.equal(body.max_tokens, 1024);
      assert.match(curl, /anthropic-version: 2023-06-01/);
    } else assert.ok(!curl.includes('anthropic-version'));
    if (protocol === 'responses') { assert.equal(body.input, '你好'); assert.equal(body.messages, undefined); }
    else assert.deepEqual(body.messages, [{role: 'user', content: '你好'}]);
    assert.ok(!curl.includes('--insecure') && !curl.includes('--retry') && !curl.includes('--location'));
  }
  assert.ok(content('trae', {protocol: 'anthropic'}).includes(origin + '/v1/messages'));
  assert.ok(content('cc-switch', {protocol: 'anthropic'}).includes('Endpoint URL：' + origin + '\n'));
  assert.ok(content('cc-switch', {protocol: 'responses'}).includes('Endpoint URL：' + origin + '/v1\n'));
  assert.ok(build('cc-switch', options())[0].title.includes('不是导入文件'));
  assert.ok(content('chatbox').includes('apiHost）：' + origin + '/v1\n'));
  assert.ok(content('chatbox').includes('apiPath）：/chat/completions'));
  assert.ok(content('chatbox', {protocol: 'responses'}).includes('apiPath）：/responses'));
  const anthropic = content('chatbox', {protocol: 'anthropic'});
  assert.match(anthropic, /Claude API Compatible/);
  assert.ok(anthropic.includes('apiHost）：' + origin + '/v1\n'));
  assert.ok(!anthropic.includes('apiPath'));
});

test('Qoder supports documented independent-app protocols without confusing it with IDE', () => {
  const qoder = guides.find(item => item.id === 'qoder');
  assert.deepEqual(qoder.protocols, ['chat', 'responses', 'anthropic']);
  assert.equal(qoder.defaultProtocol, 'chat');
  const result = build('qoder', options({key: 'copy-only-secret', model: 'selected-model'}));
  assert.equal(result[0].language, 'text');
  assert.match(result[0].title, /不适用于 IDE/);
  assert.match(result[0].content, /适用客户端：独立 Qoder 应用/);
  assert.ok(result[0].content.includes(origin + '/v1'));
  assert.ok(result[0].content.includes('copy-only-secret'));
  const anthropic = content('qoder', {protocol: 'anthropic'});
  assert.match(anthropic, /Custom → Anthropic Compatible/);
  assert.ok(anthropic.includes(origin + '/v1'));
  assert.ok(!anthropic.includes('API 类型'));
});

test('reject malformed origin before URL normalisation instead of fixing unsafe input', () => {
  for (const value of [
    'ftp://fixture.test', 'file:///tmp', 'javascript:alert(1)', '//fixture.test', 'fixture.test',
    'https://', 'https://fixture.test:65536', 'https://fixture.test:-1', 'https://fixture.test:',
    'https://user:pass@fixture.test', 'https://@fixture.test', 'https://user@fixture.test',
    origin + '/v1', origin + '/.', origin + '/..', origin + '/%2e', origin + '//',
    origin + '?', origin + '?token=ignored', origin + '#', origin + '#ignored',
    'https://fixture.test\\evil', 'https:/fixture.test', ' ' + origin, origin + ' ',
    'https://fixture.test\n', 'https://fixture.test\t', 'https://fix ture.test',
  ]) assert.throws(() => build('curl', options({origin: value})), /站点地址/);
});

test('invalid options and unsupported protocol fail closed with no echoed secrets', () => {
  for (const id of ['missing', '__proto__', 'constructor', '', null, 123]) assert.throws(() => build(id, options()));
  for (const value of [undefined, null, [], 'options']) assert.throws(() => build('curl', value));
  for (const field of ['origin', 'model', 'key', 'platform']) {
    for (const value of ['', ' ', null, {}, 123]) assert.throws(() => build('curl', options({[field]: value})));
  }
  for (const platform of ['win32', 'sh', 'POSIX']) assert.throws(() => build('curl', options({platform})));
  for (const item of guides) {
    for (const protocol of item.protocols) assert.ok(build(item.id, options({protocol})).length);
    for (const protocol of ['chat', 'responses', 'anthropic', 'unknown', null, 1].filter(value => !item.protocols.includes(value))) {
      assert.throws(() => build(item.id, options({protocol})));
    }
  }
  const secret = 'do-not-echo-secret\n';
  assert.throws(() => build('codex', options({key: secret})), error => !error.message.includes('do-not-echo-secret'));
});

test('control characters, bidi controls and invalid Unicode are rejected in all string inputs', () => {
  for (const char of ['\0', '\n', '\r', '\t', '\x1b', '\x7f', '\x85', '\u2028', '\u2029', '\u202e', '\u2066', '\ud800', '\udfff']) {
    for (const field of ['origin', 'model', 'key', 'platform', 'protocol']) assert.throws(() => build('curl', options({[field]: 'fixture' + char + 'value'})));
    assert.throws(() => build('curl' + char, options()));
  }
  assert.equal(jsonBlock({model: '模型-😀', key: '测试-🔒'}).models.providers.newapi.models[0].id, '模型-😀');
});

test('JSON and TOML escape literals and never replace API_KEY in another field', () => {
  const model = 'model-API_KEY-"quoted"\\path-\'single\'-中文';
  const key = 'literal-"quote"\\slash-\'single\'-$HOME-`echo bad`';
  const claw = jsonBlock({model, key});
  assert.equal(claw.models.providers.newapi.apiKey, key);
  assert.equal(claw.models.providers.newapi.models[0].id, model);
  assert.equal(claw.agents, undefined);
  assert.equal(claw.models.providers.newapi.baseUrl, origin + '/v1');
  const codex = build('codex', options({model, key}));
  // Generated TOML basic-string escapes are the JSON-compatible subset;
  // controls and unpaired surrogates cannot reach this serializer.
  assert.ok(!codex[0].content.includes(model));
  assert.equal(JSON.parse(codex[0].content.split('\n').find(line => line.startsWith('base_url = ')).slice('base_url = '.length)), origin + '/v1');
  assert.ok(!codex[0].content.includes(key));
  assert.match(codex[0].content, /wire_api = "responses"\nrequires_openai_auth = true/);
  for (const id of ['cursor', 'trae', 'chatbox']) {
    const output = content(id, {model, key});
    assert.ok(output.includes(model) && output.includes(key));
    assert.equal(output.split(key).length - 1, 1);
    assert.ok(output.includes(origin));
  }
});

test('OpenClaw environment-looking literals cannot interpolate credentials into model fields', () => {
  const model = 'API_KEY-${NEW_API_MODEL_KEY}-$${OTHER}';
  const key = '${KEY_VALUE}-raw';
  const claw = jsonBlock({model, key});
  const provider = claw.models.providers.newapi;
  const decodeLiteral = value => value.replace(/\$\$\{([A-Z_][A-Z0-9_]*)\}/g, (_, name) => '${' + name + '}');
  assert.equal(provider.apiKey, '$${KEY_VALUE}-raw');
  assert.equal(decodeLiteral(provider.apiKey), key);
  assert.equal(decodeLiteral(provider.models[0].id), model);
  assert.equal(claw.agents, undefined);
  assert.ok(!provider.models[0].id.includes(key));
});

test('POSIX quotes round-trip shell metacharacters using only a mocked curl builtin', () => {
  const model = 'API_KEY-\'"\\$HOME-$(false)-`false`-;|&< >-中文';
  const key = 'key-\'"\\$HOME-$(false)-`false`-;|&< >';
  for (const protocol of ['chat', 'responses', 'anthropic']) {
    // This function shadows curl: it only prints argument bytes. No external
    // binary can be resolved, and no inherited environment/config is used.
    const script = 'curl() { printf "%s\\000" "$@"; }\n' + content('curl', {model, key, protocol});
    const args = execFileSync('/bin/sh', ['-c', script], {env: {PATH: '/nonexistent'}, encoding: 'utf8'}).split('\0').slice(0, -1);
    const body = JSON.parse(args[args.indexOf('--data-raw') + 1]);
    assert.equal(body.model, model);
    assert.ok(args.includes(protocol === 'anthropic' ? 'x-api-key: ' + key : 'Authorization: Bearer ' + key));
    assert.ok(!body.model.includes(key));
  }
  for (const [id, name] of [['claude-code', 'ANTHROPIC_API_KEY']]) {
    const shell = build(id, options({model, key})).find(part => part.language === 'bash').content;
    const output = execFileSync('/bin/sh', ['-c', shell + '\nprintf "%s" "$' + name + '"'], {env: {PATH: '/nonexistent'}, encoding: 'utf8'});
    assert.equal(output, key);
  }
});

test('PowerShell uses single-quoted literals and version-safe curl.exe argument passing', () => {
  const key = 'key-\'"\\$env:HOME-$(throw "bad")-`n';
  const model = 'model-\'"\\${API_KEY}-`n';
  const psLiteral = value => "'" + value.replace(/'/g, "''") + "'";
  const codex = build('codex', options({platform: 'powershell', key, model}));
  assert.equal(JSON.parse(codex[1].content).OPENAI_API_KEY, key);
  const claude = content('claude-code', {platform: 'powershell', key, model});
  assert.ok(claude.includes('$env:ANTHROPIC_API_KEY = ' + psLiteral(key)));
  assert.ok(claude.includes('claude --model=' + psLiteral(model)));
  assert.ok(!claude.includes('ANTHROPIC_MODEL'));
  for (const protocol of ['chat', 'responses', 'anthropic']) {
    const curl = content('curl', {platform: 'powershell', key, model, protocol});
    assert.match(curl, /curl\.exe --fail-with-body/);
    assert.match(curl, /PSVersionTable.*7\.3/);
    assert.match(curl, /PSNativeCommandArgumentPassing = 'Standard'/);
    assert.ok(curl.includes(psLiteral(protocol === 'anthropic' ? 'x-api-key: ' + key : 'Authorization: Bearer ' + key)));
    const encoded = curl.split('--data-raw ')[1];
    const decoded = encoded.slice(1, -1).replace(/''/g, "'");
    assert.equal(JSON.parse(decoded).model, model);
  }
});

test('PowerShell typographic single quotes are escaped, not treated as shell delimiters', () => {
  const key = "key‘; throw 'must remain literal'; ’‚‛";
  const model = 'model‘’‚‛“”';
  // Mirrors the official tokenizer's single-quoted literal rule: any of the
  // five single quotes must be followed by a second single quote to be data.
  const decode = literal => {
    assert.equal(literal[0], "'");
    let value = '';
    for (let index = 1; index < literal.length; index++) {
      const char = literal[index];
      if (/['\u2018-\u201b]/u.test(char)) {
        if (index === literal.length - 1) return value;
        assert.ok(/['\u2018-\u201b]/u.test(literal[++index]), 'unexpected unescaped delimiter');
        value += literal[index];
      } else value += char;
    }
    assert.fail('missing closing quote');
  };
  const shell = content('claude-code', {platform: 'powershell', model, key}).split('\n');
  assert.equal(decode(shell.find(line => line.startsWith('$env:ANTHROPIC_API_KEY = ')).split(' = ')[1]), key);
  assert.equal(decode(shell.find(line => line.startsWith('claude --model=')).slice('claude --model='.length)), model);
  const curl = content('curl', {platform: 'powershell', model, key});
  assert.equal(JSON.parse(decode(curl.split('--data-raw ')[1])).model, model);
});

test('browser module exports only pure builders and performs no UI, network or storage access', () => {
  const source = fs.readFileSync(path.join(__dirname, '../src/new_api_cockpit/static/manual-guides.js'), 'utf8');
  const forbidden = () => { throw new Error('side effect forbidden'); };
  const window = {};
  const context = {window, URL, fetch: forbidden, XMLHttpRequest: forbidden, setTimeout: forbidden};
  for (const name of ['document', 'navigator', 'localStorage', 'sessionStorage']) Object.defineProperty(context, name, {get: forbidden});
  vm.runInNewContext(source, context);
  assert.deepEqual(Object.keys(window.CockpitManualGuides), ['guides', 'resolve', 'build', 'buildMcp']);
  assert.equal(window.CockpitManualGuides.guides.length, 15);
  for (const item of window.CockpitManualGuides.guides) assert.ok(window.CockpitManualGuides.build(item.id, options()).length);
  assert.ok(Object.isFrozen(window.CockpitManualGuides));
});

test('connection configs never pin a default model, role mapping, fallback or model policy', () => {
  for (const model of ['fixture-glm', 'fixture-claude', 'fixture-gpt']) {
    for (const item of guides) for (const protocol of item.protocols) {
      const output = build(item.id, options({model, protocol}));
      for (const part of output.filter(part => part.language === 'json' || part.language === 'toml')) {
        assert.doesNotMatch(part.content, /(?:^|\n)model\s*=|ANTHROPIC_(?:MODEL|DEFAULT_.*_MODEL)|"(?:agents|defaults|primary|fallbacks|modelPolicy)"\s*:/);
        assert.doesNotMatch(part.content, /web_search|model_reasoning_effort|model_context_window/);
      }
      assert.doesNotMatch(output.map(part => part.content).join('\n'), /ANTHROPIC_MODEL|ANTHROPIC_DEFAULT_/);
    }
  }
  const claude = JSON.parse(build('claude-code', options({variant: 'vscode'}))[0].content);
  assert.deepEqual(claude, {'claudeCode.environmentVariables': [
    {name: 'ANTHROPIC_BASE_URL', value: origin}, {name: 'ANTHROPIC_API_KEY', value: 'API_KEY'},
    {name: 'ANTHROPIC_CUSTOM_MODEL_OPTION', value: 'fixture-model'},
  ]});
});

test('CC Switch target and upstream format produce native or routing instructions, never model mapping', () => {
  for (const target of ['claude-code', 'codex']) for (const protocol of ['chat', 'responses', 'anthropic']) {
    const output = build('cc-switch', options({target, protocol}));
    const direct = protocol === (target === 'codex' ? 'responses' : 'anthropic');
    assert.match(output[0].content, direct ? /Direct（原生直连）/ : /Routing（启动本地路由/);
    assert.doesNotMatch(output[0].content, /fixture-model|默认模型|模型映射|wire_api/);
    assert.equal(output[1].containsKey, false);
    assert.match(output[1].content, new RegExp('^' + (target === 'codex' ? 'codex' : 'claude') + ' --model='));
  }
  assert.throws(() => build('cc-switch', options({target: 'missing'})), /目标/);
});

test('one-off launch flags preserve any selected model literally without execution', () => {
  const model = '--model-\'"\\$HOME-$(false)-`false`-;|&< >-中文';
  for (const id of ['claude-code', 'codex', 'cc-switch']) {
    const part = build(id, options({model})).find(part => part.containsKey === false && part.language === 'bash');
    assert.ok(part);
    const script = 'claude() { printf "%s\\000" "$@"; }; codex() { printf "%s\\000" "$@"; };\n' + part.content;
    const args = execFileSync('/bin/sh', ['-c', script], {env: {PATH: '/nonexistent'}, encoding: 'utf8'}).split('\0').slice(0, -1);
    assert.deepEqual(args, ['--model=' + model]);
  }
});

test('official screenshot metadata references real bundled WebP assets and a primary source', () => {
  const pictures = guides.flatMap(item => item.steps.filter(step => step.image).map(step => step.image));
  assert.equal(pictures.length, 2);
  for (const picture of pictures) {
    assert.ok(picture.alt && picture.caption && picture.source === 'https://docs.trae.cn/ide_models');
    const local = path.join(__dirname, '../src/new_api_cockpit/static', picture.src.slice('/cockpit/static/'.length));
    const bytes = fs.readFileSync(local);
    assert.equal(bytes.subarray(0, 4).toString(), 'RIFF');
    assert.equal(bytes.subarray(8, 12).toString(), 'WEBP');
    assert.ok(picture.width > 0 && picture.height > 0);
  }
});

test('every system, surface and protocol has exactly one placement for each generated block', () => {
  let cases = 0;
  for (const guide of guides) for (const platform of ['macos', 'windows', 'linux']) {
    assert.deepEqual(guide.platforms, ['macos', 'windows', 'linux']);
    assert.equal(guide.checkedAt, '2026-10-30');
    for (const surface of guide.variants || [{id: ''}]) {
      for (const protocol of resolve(guide.id, {platform, variant: surface.id}).protocols) {
        const opts = {platform, variant: surface.id, protocol};
        const tutorial = resolve(guide.id, opts), result = build(guide.id, options(opts));
        assert.equal(tutorial.title, guide.title);
        assert.ok(tutorial.platformHint && tutorial.installURL);
        assert.ok(tutorial.steps.every(step => !/\{(?:codexDir|hermesFile|terminalHint|piFile|buddyFile|permissionHint)/.test(step.body)));
        assert.deepEqual(tutorial.steps.flatMap(step => step.snippets || []).sort((a,b) => a-b), result.map((_, i) => i));
        for (const part of result) if (part.fields) {
          assert.equal(part.containsKey, part.fields.some(field => field.containsKey));
          for (const field of part.fields) assert.equal(field.containsKey, field.value === 'API_KEY');
        }
        cases++;
      }
    }
  }
  assert.equal(cases, 114);
});

test('CLI and GUI surfaces do not mix each other’s configuration steps', () => {
  const cli = build('claude-code', options({variant:'cli'}));
  assert.equal(cli.length, 2);assert.ok(cli.every(block => block.language === 'bash'));
  const vscode = build('claude-code', options({variant:'vscode',platform:'windows'}));
  assert.equal(vscode.length, 1);assert.equal(vscode[0].language, 'json');
  assert.equal(vscode[0].destination, '%APPDATA%\\Code\\User\\settings.json');
  for (const variant of ['desktop','vscode']) {
    const guide = resolve('codex', {variant});
    assert.equal(build('codex',options({variant})).length,2);
    assert.ok(!guide.steps.some(step => step.snippets?.includes(2)));
  }
  assert.throws(() => resolve('codex',{variant:'unknown'}));
});

test('Pi, DeepSeek Harness and CodeBuddy honor distinct protocol and address contracts', () => {
  for (const protocol of ['chat','responses','anthropic']) {
    const pi = JSON.parse(build('pi-agent',options({protocol}))[1].content).providers.newapi;
    assert.equal(pi.api,{chat:'openai-completions',responses:'openai-responses',anthropic:'anthropic-messages'}[protocol]);
    assert.equal(pi.baseUrl,origin+(protocol==='anthropic'?'':'/v1'));
    const harness = build('deepseek-harness',options({protocol}));
    assert.equal(harness[0].containsKey,false);
    assert.equal(harness[1].fields.find(field=>field.label==='基础 URL').value,pi.baseUrl);
    assert.equal(harness[2].fields.find(field=>field.label==='模型 ID').value,'fixture-model');
    const buddy = build('codebuddy',options({protocol,variant:'cli'}));
    if(protocol==='anthropic') {
      assert.equal(buddy[0].language,'bash');assert.ok(buddy[0].content.includes("CODEBUDDY_BASE_URL='"+origin+"'"));
    } else {
      const config=JSON.parse(buddy[0].content);assert.equal(config.models[0].url,origin+(protocol==='responses'?'/v1':'/v1/chat/completions'));
      assert.equal(config.models[0].api,protocol==='responses'?'openai-responses':undefined);
      assert.equal(config.availableModels,undefined);
    }
  }
  assert.throws(()=>build('codebuddy',options({variant:'ide',protocol:'responses'})));
  assert.throws(()=>build('codebuddy',options({key:'${DO_NOT_EXPAND}'})),error=>!error.message.includes('DO_NOT_EXPAND'));
  const key='!command-$NAME-${SECRET}-$$';
  const pi=JSON.parse(build('pi-agent',options({key}))[1].content).providers.newapi;
  assert.equal(pi.apiKey,'$!command-$$NAME-$${SECRET}-$$$$');
});

test('TOML parses natively and Hermes YAML uses a bounded mapping with literal scalars on all systems', () => {
  const python=process.env.COCKPIT_PYTHON||(fs.existsSync(path.join(__dirname,'../.venv/bin/python'))?path.join(__dirname,'../.venv/bin/python'):'python3');
  const fixtures=[];
  for(const platform of ['macos','windows','linux'])for(const protocol of ['chat','responses','anthropic']){
    fixtures.push({protocol,toml:build('codex',options({platform}))[0].content});
    const yaml=build('hermes',options({platform,protocol,key:'fixture-"quote"-\\path'}))[0].content.split('\n');
    assert.deepEqual(yaml.slice(0,2),['providers:','  newapi:']);assert.equal(yaml.length,5);
    assert.equal(JSON.parse(yaml[2].slice('    api: '.length)),origin+(protocol==='anthropic'?'':'/v1'));
    assert.equal(yaml[3],'    transport: '+{chat:'chat_completions',responses:'codex_responses',anthropic:'anthropic_messages'}[protocol]);
    assert.equal(JSON.parse(yaml[4].slice('    api_key: '.length)),'fixture-"quote"-\\path');
  }
  const script='import json,sys,tomllib\n'
    +'for row in json.load(sys.stdin):\n'
    +' c=tomllib.loads(row["toml"]); p=c["model_providers"]["newapi"]\n'
    +' assert c["cli_auth_credentials_store"]=="file" and p["wire_api"]=="responses"\n';
  execFileSync(python,['-c',script],{input:JSON.stringify(fixtures),encoding:'utf8'});
});
