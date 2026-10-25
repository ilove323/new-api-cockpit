const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {createHash} = require('node:crypto');

test('download export replaces generated old packages without deleting unrelated files', t => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'cockpit-vsix-export-'));
  t.after(() => fs.rmSync(root, {recursive: true, force: true}));
  fs.mkdirSync(path.join(root, 'scripts'));
  fs.copyFileSync(path.join(__dirname, '../scripts/export-download.cjs'), path.join(root, 'scripts/export-download.cjs'));
  fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({name: 'new-api-cockpit-balance', version: '9.8.7'}));
  const filename = 'new-api-cockpit-balance-9.8.7.vsix';
  const bytes = Buffer.concat([Buffer.from('504b0304', 'hex'), Buffer.from('synthetic-package')]);
  fs.writeFileSync(path.join(root, filename), bytes);
  const output = path.join(root, 'output'); fs.mkdirSync(output);
  fs.writeFileSync(path.join(output, 'new-api-cockpit-balance-1.2.3.vsix'), 'old-generated-package');
  fs.writeFileSync(path.join(output, 'unrelated.txt'), 'keep');
  execFileSync(process.execPath, [path.join(root, 'scripts/export-download.cjs'), output]);
  assert.deepEqual(fs.readdirSync(output).sort(), [filename, 'unrelated.txt', 'vscode-extension.json'].sort());
  assert.deepEqual(fs.readFileSync(path.join(output, filename)), bytes);
  assert.equal(fs.readFileSync(path.join(output, 'unrelated.txt'), 'utf8'), 'keep');
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(output, 'vscode-extension.json'), 'utf8')), {
    version: '9.8.7', filename, size: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex')
  });
});
