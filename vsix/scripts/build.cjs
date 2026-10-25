'use strict';
const {execFileSync} = require('node:child_process');
const {buildSync} = require('esbuild');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
// Deleted source modules must not survive in a later VSIX as stale JavaScript.
fs.rmSync(path.join(root, 'dist'), {recursive: true, force: true});
execFileSync(process.execPath, [require.resolve('typescript/bin/tsc'), '-p', root], {stdio: 'inherit'});
// Only this module uses third-party parsers. Ship their bundled code, not a
// node_modules tree or a runtime package-install step.
buildSync({absWorkingDir: root, entryPoints: ['src/client-config.ts'], outfile: 'dist/client-config.js', bundle: true,
  platform: 'node', target: 'node20', format: 'cjs', mainFields: ['module', 'main'], external: ['./client'], legalComments: 'eof'});
