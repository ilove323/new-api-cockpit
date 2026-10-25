'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {fetchBalance} = require('../dist/client');
const root = path.resolve(__dirname, '../..');
const python = process.env.COCKPIT_PYTHON || (fs.existsSync(path.join(root, '.venv/bin/python')) ? path.join(root, '.venv/bin/python') : undefined);
test('extension reads an exact balance from the real Cockpit MCP handler with a synthetic identity', {skip: !python, timeout: 15000}, async t => {
  const child = spawn(python, [path.join(__dirname, 'mcp_fixture.py')], {cwd: root, env: {...process.env, PYTHONPATH: path.join(root, 'src'), COCKPIT_MCP_ENABLED: 'true'}});
  const exited = once(child, 'exit');
  t.after(async () => { child.kill(); await exited; });
  const port = await new Promise((resolve, reject) => {
    let line = ''; child.stdout.on('data', chunk => { line += chunk; if (line.includes('\n')) resolve(Number(line.split('\n')[0])); });
    child.once('error', reject); child.once('exit', () => reject(new Error('Fixture startup failed')));
  });
  assert(Number.isInteger(port) && port > 0);
  const result = await fetchBalance({endpoint: 'http://127.0.0.1:' + port + '/cockpit/mcp', pat: 'nap_vscode_fixture_user_pat_only'}, new AbortController().signal);
  assert.deepEqual(result.user, {id: 12, username: 'fixture-user'});
  assert.equal(result.remaining_amount, '2.500002'); assert.equal(result.used_amount, '1'); assert.equal(result.request_count, 7);
  assert.equal(result.quota_per_unit, 500000);
});
