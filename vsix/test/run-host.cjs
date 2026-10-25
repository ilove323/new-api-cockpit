'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawn} = require('node:child_process');
const root = path.resolve(__dirname, '..');
const executable = process.env.VSCODE_EXECUTABLE_PATH || (process.platform === 'darwin' ? '/Applications/Visual Studio Code.app/Contents/MacOS/Code' : 'code');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'cv-host-'));
const development = path.join(temporary, 'extension'); fs.mkdirSync(development);
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
manifest.main = './host-entry.cjs';
fs.writeFileSync(path.join(development, 'package.json'), JSON.stringify(manifest));
fs.cpSync(path.join(root, 'dist'), path.join(development, 'dist'), {recursive: true});
fs.cpSync(path.join(root, 'media'), path.join(development, 'media'), {recursive: true});
fs.writeFileSync(path.join(development, 'host-entry.cjs'),
  "exports.activate = context => { globalThis.__cockpitTestExtensionContext = context; return require('./dist/extension').activate(context); };\n");
const env = {...process.env}; delete env.ELECTRON_RUN_AS_NODE;
env.COCKPIT_TEST_USER_DATA_DIR = path.join(temporary, 'user');
const child = spawn(executable, ['--new-window', '--disable-extensions', '--disable-workspace-trust', '--skip-welcome', '--skip-release-notes',
  '--user-data-dir=' + path.join(temporary, 'user'), '--extensions-dir=' + path.join(temporary, 'extensions'),
  '--extensionDevelopmentPath=' + development, '--extensionTestsPath=' + path.join(__dirname, 'host.cjs')], {env, stdio: 'inherit'});
const timer = setTimeout(() => child.kill(), 60000);
function finish(code) { clearTimeout(timer); fs.rmSync(temporary, {recursive: true, force: true, maxRetries: 5, retryDelay: 200}); process.exitCode = code; }
child.on('error', () => { console.error('无法启动 VS Code；请设置 VSCODE_EXECUTABLE_PATH。'); finish(1); });
child.on('exit', code => finish(code === 0 ? 0 : 1));
