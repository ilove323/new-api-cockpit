'use strict';
const assert = require('node:assert/strict');
const vscode = require('vscode');
const http = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const {KeysClient} = require('../dist/keys-client');
const {requestOwnJson, normalizeEndpoint} = require('../dist/client');
const {claudeConfig, codexConfig, codexAuth} = require('../dist/client-config');
const {DraftEditor, ConfigDrafts} = require('../dist/config-drafts');
exports.run = async () => {
  const extension = vscode.extensions.getExtension('ilove323.new-api-cockpit-balance');
  assert(extension, 'Extension discovered by VS Code'); await extension.activate(); assert(extension.isActive);
  const commands = await vscode.commands.getCommands(true);
  for (const name of ['connect', 'refresh', 'show', 'disconnect', 'keysRefresh', 'keysCreate', 'keysGroup', 'keysDelete', 'keysCopy', 'keysConfigure', 'configSave']) assert(commands.includes('newApiCockpitBalance.' + name));
  assert.equal(commands.includes('newApiCockpitBalance.keysReconcile'), false);
  assert.equal(vscode.workspace.getConfiguration('newApiCockpitBalance').get('refreshIntervalMinutes'), 15);
  assert.equal(vscode.workspace.getConfiguration('newApiCockpitBalance').get('pat'), undefined);
  await vscode.commands.executeCommand('newApiCockpitBalance.account.focus');
  await vscode.commands.executeCommand('newApiCockpitBalance.keys.focus');
  const views = extension.packageJSON.contributes.views.newApiCockpitBalance;
  assert.deepEqual(views.map(view => view.id), ['newApiCockpitBalance.account', 'newApiCockpitBalance.keys']);
  const requests = [], server = http.createServer((req, res) => {
    requests.push(req.method);
    res.writeHead(200, {'Content-Type': 'application/json; charset=utf-8'});
    res.end(JSON.stringify({success: true, message: ''}));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const client = new KeysClient({endpoint: 'http://127.0.0.1:' + server.address().port + '/cockpit/mcp', pat: 'nap_vscode_host_fixture_only'});
    await client.remove(7, new AbortController().signal);
    assert.deepEqual(requests, ['DELETE']);
  } finally {await new Promise(resolve => {server.closeAllConnections(); server.close(resolve);});}
  console.log('Native VS Code HTTP DELETE response handling verified with a synthetic owner API.');
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'cockpit-config-drafts-'));
  const settings = vscode.workspace.getConfiguration('files');
  const oldAutoSave = settings.inspect('autoSave')?.globalValue, oldDelay = settings.inspect('autoSaveDelay')?.globalValue;
  const draftEditor = new DraftEditor();
  const opened = [], key = 'sk-host_draft_fixture_not_a_real_key';
  try {
    await settings.update('autoSave', 'afterDelay', vscode.ConfigurationTarget.Global);
    await settings.update('autoSaveDelay', 100, vscode.ConfigurationTarget.Global);
    const actualContext = globalThis.__cockpitTestExtensionContext;
    assert(actualContext && process.env.COCKPIT_TEST_USER_DATA_DIR, 'real activation context captured by the isolated test entry');
    console.log('Native VS Code real globalStorageUri scheme:', actualContext.globalStorageUri.scheme);
    const settingsPath = vscode.Uri.joinPath(actualContext.globalStorageUri.with({scheme: 'file'}), '..', '..', 'settings.json');
    const relative = path.relative(process.env.COCKPIT_TEST_USER_DATA_DIR, settingsPath.fsPath);
    assert(relative && !relative.startsWith('..') && !path.isAbsolute(relative), 'only the isolated test profile may be touched');
    const beforeSettings = await fs.readFile(settingsPath.fsPath, 'utf8');
    // Accept the first item in the real native Quick Pick. The filesystem path
    // and ExtensionContext are not mocked, which catches userdata URI schemes.
    const accept = setInterval(() => {void vscode.commands.executeCommand('workbench.action.acceptSelectedQuickOpenItem');}, 150);
    let prepare;
    try {prepare = await new ConfigDrafts(actualContext, draftEditor).prepare('claude', 'https://gateway.example.com');}
    finally {clearInterval(accept);}
    assert(prepare); await prepare(key);
    const claudeDocument = vscode.window.activeTextEditor.document; opened.push(claudeDocument);
    assert(claudeDocument.isUntitled && claudeDocument.isDirty);
    assert(claudeDocument.getText().includes(key));
    await new Promise(resolve => setTimeout(resolve, 250));
    assert.equal(await fs.readFile(settingsPath.fsPath, 'utf8'), beforeSettings);
    await draftEditor.save(claudeDocument.uri);
    assert(JSON.parse(await fs.readFile(settingsPath.fsPath, 'utf8'))['claudeCode.environmentVariables'].some(v => v.name === 'ANTHROPIC_AUTH_TOKEN' && v.value === key));
    console.log('Native VS Code Claude configuration verified using the real activation context, native target picker, unsaved draft and explicit Save in an isolated profile.');
    const originals = [
      {file: 'settings.json', content: '{"editor.fontSize":17}', language: 'jsonc', build: text => claudeConfig(text, 'https://gateway.example.com', key)},
      {file: 'config.toml', content: '# keep\nmodel="keep-model"\n', language: 'toml', build: text => codexConfig(text, 'https://gateway.example.com')},
      {file: 'auth.json', content: '{"tokens":{"refresh_token":"old-fixture"}}', language: 'json', build: text => codexAuth(text, key)}
    ];
    for (const original of originals) await fs.writeFile(path.join(temporary, original.file), original.content);
    const drafts = originals.map(original => ({target: vscode.Uri.file(path.join(temporary, original.file)), language: original.language, content: original.build(original.content), source: original.content, label: original.file, sensitive: original.file !== 'config.toml'}));
    const existingDrafts = await draftEditor.open(drafts); opened.push(...existingDrafts);
    assert(existingDrafts.every(document => document.isUntitled && document.isDirty && document.uri.scheme === 'untitled'));
    await new Promise(resolve => setTimeout(resolve, 500));
    for (const original of originals) assert.equal(await fs.readFile(path.join(temporary, original.file), 'utf8'), original.content);
    assert.throws(() => draftEditor.ensureAvailable(drafts.map(d => d.target)), /未保存草稿/);
    // Simulate a user's edit in the native text editor, not a source-file edit.
    const auth = existingDrafts[2], edit = new vscode.WorkspaceEdit();
    edit.replace(auth.uri, new vscode.Range(auth.positionAt(0), auth.positionAt(auth.getText().length)), codexAuth('', key + '_edited'));
    assert(await vscode.workspace.applyEdit(edit));
    await settings.update('autoSave', 'onFocusChange', vscode.ConfigurationTarget.Global);
    await vscode.window.showTextDocument(existingDrafts[0]);
    await new Promise(resolve => setTimeout(resolve, 250));
    assert(auth.isDirty); assert.equal(await fs.readFile(path.join(temporary, 'auth.json'), 'utf8'), originals[2].content);
    // Exercise exactly the draft save handler used by the toolbar/shortcut,
    // after the user has edited the JSON. Only explicit saves may write disk.
    await draftEditor.save(auth.uri);
    assert.equal(JSON.parse(await fs.readFile(path.join(temporary, 'auth.json'), 'utf8')).OPENAI_API_KEY, key + '_edited');
    if (process.platform !== 'win32') assert.equal((await fs.stat(path.join(temporary, 'auth.json'))).mode & 0o777, 0o600);
    await fs.writeFile(path.join(temporary, 'config.toml'), '# changed externally\n');
    await assert.rejects(draftEditor.save(existingDrafts[1].uri), /已变化/);
    assert.equal(await fs.readFile(path.join(temporary, 'config.toml'), 'utf8'), '# changed externally\n');
    const fresh = vscode.Uri.file(path.join(temporary, 'new-home', 'auth.json'));
    const [newDocument] = await draftEditor.open([{target: fresh, language: 'json', content: codexAuth('', key), source: '', label: 'new auth.json', sensitive: true}]);
    opened.push(newDocument);
    await new Promise(resolve => setTimeout(resolve, 250));
    await assert.rejects(fs.stat(fresh.fsPath), error => error.code === 'ENOENT');
    await draftEditor.save(newDocument.uri);
    assert.equal(JSON.parse(await fs.readFile(fresh.fsPath, 'utf8')).OPENAI_API_KEY, key);
    console.log('Native VS Code draft checks passed: existing files unchanged under delay/focus Auto Save, editable drafts, duplicate protection, explicit Save overwrites the intended target, concurrent changes blocked, new directory/file created only on Save.');
  } finally {
    for (const document of opened) if (!document.isClosed && document.isUntitled) {
      await vscode.window.showTextDocument(document); await vscode.commands.executeCommand('workbench.action.revertAndCloseActiveEditor');
    }
    draftEditor.dispose();
    await settings.update('autoSave', oldAutoSave, vscode.ConfigurationTarget.Global);
    await settings.update('autoSaveDelay', oldDelay, vscode.ConfigurationTarget.Global);
    await fs.rm(temporary, {recursive: true, force: true});
  }
  if (process.env.COCKPIT_DIAGNOSTIC_ORIGIN) {
    const connection = {endpoint: normalizeEndpoint(process.env.COCKPIT_DIAGNOSTIC_ORIGIN), pat: 'nap_diagnostic_invalid_not_a_real_token'};
    // Deliberately invalid authentication: exercise the real TLS/HTTP path but
    // never authorize a production write or expose a personal credential.
    let step = 0;
    for (const method of ['GET', 'PUT', 'GET', 'PUT', 'GET', 'GET']) {
      step++;
      await assert.rejects(requestOwnJson(connection, {method, signal: new AbortController().signal, scope: 'api_key:read',
        path: method === 'GET' ? '/api/token/?p=1&page_size=50' : '/api/token/', ...(method === 'PUT' ? {body: {id: 0}} : {})}), error => {
        console.log('LIVE_HTTPS_RESULT', step, method, error.status || error.message); return error.status === 401;
      });
    }
    console.log('Native VS Code live HTTPS query/write/query path: six expected unauthenticated 401 responses, no TLS resets or authorized writes.');
  }
  console.log('VS Code account/KEY sections, native divider, activation and commands verified with an isolated empty profile.');
};
