'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const path = require('node:path');
const documents = [], files = new Map();
let reads = 0, writes = 0, pick;
class Uri {
  constructor(file, scheme = 'file') {this.path = this.fsPath = file; this.scheme = scheme;}
  static file(file) {return new Uri(file);}
  static joinPath(uri, ...segments) {return new Uri(path.posix.join(uri.path, ...segments), uri.scheme);}
  with({scheme}) {return new Uri(this.path, scheme);}
}
class FileSystemError extends Error {constructor(code) {super(code); this.code = code;}}
const vscode = {Uri, FileSystemError, FileType: {Directory: 2}, ViewColumn: {Active: -1, Beside: -2}, env: {},
  Position: class {constructor(line, character) {this.line = line; this.character = character;}},
  WorkspaceEdit: class {insert(uri, _position, content) {this.uri = uri; this.content = content;}},
  workspace: {textDocuments: documents,
    fs: {async stat(uri) {reads++; if (!files.has(uri.path)) throw new FileSystemError('FileNotFound'); return {size: Buffer.byteLength(files.get(uri.path))};},
      async readFile(uri) {reads++; return Buffer.from(files.get(uri.path));},
      async readDirectory() {return [];}, async writeFile() {writes++; assert.fail('preparing or opening drafts must never write a target file');}},
    async openTextDocument(uri) {const doc = {uri, isUntitled: true, isDirty: true, isClosed: false}; documents.push(doc); return doc;},
    async applyEdit(edit) {documents.find(doc => doc.uri.path === edit.uri.path).text = edit.content; return true;}},
  languages: {async getLanguages() {return ['json', 'jsonc'];}, async setTextDocumentLanguage(doc, language) {doc.languageId = language; return doc;}},
  window: {async showTextDocument() {}, async showInformationMessage() {}, async showQuickPick(items) {return pick?.(items);}}
};
const original = Module._load;
Module._load = function (id, ...args) {return id === 'vscode' ? vscode : original.call(this, id, ...args);};
let ConfigDrafts, codexHome;
try {({ConfigDrafts, codexHome} = require('../dist/config-drafts'));} finally {Module._load = original;}
const KEY = 'sk-configuration_fixture_not_real_1234';
function reset() {documents.length = 0; files.clear(); reads = writes = 0; vscode.env.remoteName = undefined; pick = items => items[0];}
const editor = {
  ensureAvailable(targets) {if (targets.some(t => documents.some(d => d.target === t.path))) throw new Error('未保存草稿');},
  async open(drafts) {for (const draft of drafts) documents.push({uri: new Uri('Untitled-' + documents.length, 'untitled'), target: draft.target.path, isUntitled: true, text: draft.content});}
};
const service = () => new ConfigDrafts(context, editor);
const context = {globalStorageUri: Uri.file('/test/Code/User/globalStorage/ilove323.new-api-cockpit-balance')};

test('Codex uses the explicit home on Linux/macOS/Windows, rather than the extension working directory', () => {
  assert.equal(codexHome('/home/demo', undefined, 'linux'), '/home/demo/.codex');
  assert.equal(codexHome('/Users/demo', '~/custom', 'darwin'), '/Users/demo/custom');
  assert.equal(codexHome('C:\\Users\\demo', 'D:\\private\\codex', 'win32'), 'D:\\private\\codex');
  assert.throws(() => codexHome('/home/demo', 'relative/path', 'linux'), /绝对路径/);
});
test('preparing Codex reveals no key; opening produces two unsaved drafts and leaves originals unchanged', async t => {
  reset(); const old = process.env.CODEX_HOME; process.env.CODEX_HOME = '/test/codex';
  t.after(() => {if (old === undefined) delete process.env.CODEX_HOME; else process.env.CODEX_HOME = old;});
  files.set('/test/codex/config.toml', '# keep\nmodel="keep-model"\n');
  files.set('/test/codex/auth.json', '{"tokens":{"refresh_token":"old-fixture"}}');
  const snapshot = [...files], open = await service().prepare('codex', 'https://gateway.example.com');
  assert.equal(documents.length, 0); assert.equal(writes, 0);
  await open(KEY);
  assert.equal(documents.length, 2); assert(documents.every(d => d.isUntitled && d.uri.scheme === 'untitled'));
  assert(documents[0].text.includes('model="keep-model"')); assert(!documents[0].text.includes(KEY));
  assert.equal(JSON.parse(documents[1].text).OPENAI_API_KEY, KEY); assert.deepEqual([...files], snapshot); assert.equal(writes, 0);
  const before = reads;
  await assert.rejects(service().prepare('codex', 'https://gateway.example.com'), /未保存草稿/);
  assert.equal(reads, before, 'do not re-read or overwrite an existing draft');
});
test('Claude reads the chosen user settings, preserves other settings and never edits the project or writes disk', async () => {
  reset(); const target = '/test/Code/User/settings.json'; files.set(target, '{"editor.fontSize":18}');
  const open = await service().prepare('claude', 'https://gateway.example.com');
  await open(KEY); assert.equal(documents.length, 1); assert.equal(documents[0].target, target);
  assert.equal(files.get(target), '{"editor.fontSize":18}'); assert.equal(writes, 0);
});
test('Claude accepts real desktop vscode-userdata storage and retains the local settings target', async () => {
  reset(); const target = '/test/Code/User/settings.json'; files.set(target, '{"editor.fontSize":18}');
  const actualSchemeContext = {globalStorageUri: new Uri('/test/Code/User/globalStorage/ilove323.new-api-cockpit-balance', 'vscode-userdata')};
  const open = await new ConfigDrafts(actualSchemeContext, editor).prepare('claude', 'https://gateway.example.com');
  await open(KEY); assert.equal(documents[0].target, target);
  assert.equal(files.get(target), '{"editor.fontSize":18}'); assert.equal(writes, 0);
});
test('non-local storage providers are not silently converted into local configuration paths', async () => {
  reset();
  const remote = {globalStorageUri: new Uri('/home/demo/storage/extension', 'vscode-remote')};
  await assert.rejects(new ConfigDrafts(remote, editor).prepare('claude', 'https://gateway.example.com'), /无法确定/);
  assert.equal(reads, 0); assert.equal(writes, 0); assert.equal(documents.length, 0);
});
test('cancel, dirty originals, malformed input and remote windows cannot leak a key into a draft or write files', async () => {
  reset(); pick = () => undefined; assert.equal(await service().prepare('claude', 'https://gateway.example.com'), undefined);
  pick = items => items[0];
  documents.push({uri: Uri.file('/test/Code/User/settings.json'), isDirty: true});
  await assert.rejects(service().prepare('claude', 'https://gateway.example.com'), /未保存修改/);
  documents.length = 0; files.set('/test/Code/User/settings.json', '{broken');
  await assert.rejects(service().prepare('claude', 'https://gateway.example.com'), /格式有误/);
  vscode.env.remoteName = 'ssh-remote';
  await assert.rejects(service().prepare('codex', 'https://gateway.example.com'), /本机窗口/);
  assert.equal(documents.length, 0); assert.equal(writes, 0);
});
