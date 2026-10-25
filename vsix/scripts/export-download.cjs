'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {createHash} = require('node:crypto');
const root = path.resolve(__dirname, '..');
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
if (manifest.name !== 'new-api-cockpit-balance' || !/^\d+\.\d+\.\d+$/.test(manifest.version)) throw new Error('Invalid extension identity/version');
const filename = manifest.name + '-' + manifest.version + '.vsix';
const bytes = fs.readFileSync(path.join(root, filename));
if (bytes.subarray(0, 4).toString('hex') !== '504b0304') throw new Error('Not a VSIX/ZIP package');
const directory = path.resolve(process.argv[2] || path.join(root, '../src/new_api_cockpit/bundled_clients'));
fs.mkdirSync(directory, {recursive: true});
fs.writeFileSync(path.join(directory, filename), bytes);
fs.writeFileSync(path.join(directory, 'vscode-extension.json'), JSON.stringify({
  version: manifest.version, filename, size: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex')
}, null, 2) + '\n');
// This generated directory ships with Python packages. Keep only this release,
// without touching unrelated files in an explicitly supplied output directory.
for (const name of fs.readdirSync(directory)) {
  if (name !== filename && /^new-api-cockpit-balance-\d+\.\d+\.\d+\.vsix$/.test(name)) {
    fs.unlinkSync(path.join(directory, name));
  }
}
process.stdout.write('Bundled ' + filename + '\n');
