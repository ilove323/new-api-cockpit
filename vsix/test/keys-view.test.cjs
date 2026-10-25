'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const Module = require('node:module');
const actions = new Map();
let picker, warning;
const errors = [];
const vscode = {
  TreeItem: class {}, ThemeIcon: class {},
  commands: {registerCommand(name, action) {actions.set(name, action); return {dispose() {}};}, async executeCommand() {}},
  window: {async showQuickPick(...args) {return picker?.(...args);}, async showWarningMessage(...args) {return warning?.(...args);},
    async showInformationMessage() {}, async showErrorMessage(message) {errors.push(message);}},
  env: {clipboard: {async writeText() {}}}
};
const originalLoad = Module._load;
Module._load = function (id, ...args) {return id === 'vscode' ? vscode : originalLoad.call(this, id, ...args);};
let registerKeysCommands;
try {({registerKeysCommands} = require('../dist/keys-view'));} finally {Module._load = originalLoad;}
const KEY = {id: 8, name: 'fixture', group: 'custom', masked: 'sk-xxxx••••yyyy'};
function fixture() {
  actions.clear(); errors.length = 0; picker = undefined; warning = undefined;
  let generation = 1; const calls = [];
  const controller = {
    state: {connected: true, busy: false, loading: false, page: {page: 1, total: 1, items: [KEY]}},
    ticket: () => generation, current: ticket => ticket === generation,
    async load(page) {calls.push(['load', page]);}, async refresh() {calls.push(['refresh']);},
    async groups() {return [{name: 'custom', description: ''}, {name: 'auto', description: ''}];},
    async remove(key) {calls.push(['remove', key.id]); return {applied: true, message: 'deleted'};},
    async changeGroup(key, group) {calls.push(['group', key.id, group]); return {applied: true, message: 'changed'};}
  };
  registerKeysCommands({subscriptions: []}, controller, () => ({}), async (kind, key) => {calls.push(['configure', kind, key.id]);});
  return {calls, switchAccount: () => generation++, run: (name, arg) => actions.get('newApiCockpitBalance.' + name)(arg)};
}
test('KEY refresh uses forced refresh; page navigation uses the cache-aware load path', async () => {
  const f = fixture(); await f.run('keysRefresh'); await f.run('keysPage', 2);
  assert.deepEqual(f.calls, [['refresh'], ['load', 2]]);
});
test('deleting a KEY sends nothing on cancel and sends one removal only after explicit confirmation', async () => {
  const f = fixture(); picker = items => items[0];
  await f.run('keysDelete'); assert.deepEqual(f.calls, []);
  warning = (_text, options, choice) => {assert.equal(options.modal, true); return choice;};
  await f.run('keysDelete'); assert.deepEqual(f.calls, [['remove', KEY.id]]);
});
test('choosing a route group applies it directly, without a second confirmation dialog', async () => {
  const f = fixture(); picker = (items, options) => options.title === '选择本人 KEY' ? items[0] : items.find(item => item.name === 'auto');
  warning = () => assert.fail('group selection must not ask for a second confirmation');
  await f.run('keysGroup'); assert.deepEqual(f.calls, [['group', KEY.id, 'auto']]);
  assert.equal(actions.has('newApiCockpitBalance.keysReconcile'), false);
});
test('an account switch while the confirmation is open prevents the old KEY operation', async () => {
  const f = fixture(); picker = items => items[0]; warning = (_text, _options, choice) => {f.switchAccount(); return choice;};
  await f.run('keysDelete'); assert.deepEqual(f.calls, []); assert.match(errors[0], /账户连接已变化/);
});
test('configuration selects a KEY then a client, without another confirmation, a write or a clipboard operation', async () => {
  for (const client of ['claude', 'codex']) {
    const f = fixture(); picker = (items, options) => options.title === '选择本人 KEY' ? items[0] : items.find(item => item.client === client);
    warning = () => assert.fail('configuration must only prepare unsaved drafts');
    await f.run('keysConfigure'); assert.deepEqual(f.calls, [['configure', client, KEY.id]]);
  }
});
test('cancel or account switch in the client picker never opens a configuration with the old KEY', async () => {
  const f = fixture(); picker = (items, options) => options.title === '选择本人 KEY' ? items[0] : undefined;
  await f.run('keysConfigure'); assert.deepEqual(f.calls, []);
  picker = (items, options) => {if (options.title !== '选择本人 KEY') f.switchAccount(); return items[0];};
  await f.run('keysConfigure'); assert.deepEqual(f.calls, []); assert.match(errors[0], /账户连接已变化/);
});
