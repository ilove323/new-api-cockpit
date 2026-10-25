import * as vscode from 'vscode';
import * as os from 'node:os';
import * as path from 'node:path';
import * as fs from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { BalanceError } from './client';
import { claudeConfig, ClientKind, codexAuth, codexConfig } from './client-config';

interface Draft {target: vscode.Uri; language: string; content: string; source: string; label: string; sensitive?: boolean}
const SAVE = 'newApiCockpitBalance.configSave';
export function codexHome(home: string, override: string | undefined, platform = process.platform): string {
  const paths = platform === 'win32' ? path.win32 : path.posix;
  let value = override?.trim() || paths.join(home, '.codex');
  if (/^~(?:[\\/]|$)/.test(value)) value = paths.join(home, value.slice(2));
  if (!paths.isAbsolute(value)) throw new BalanceError('CODEX_HOME 必须是绝对路径，请修正后重开 VS Code。');
  return paths.normalize(value);
}
async function read(target: vscode.Uri): Promise<string> {
  const open = vscode.workspace.textDocuments.find(doc => doc.uri.scheme !== 'untitled' && doc.uri.fsPath === target.fsPath);
  if (open?.isDirty) throw new BalanceError('目标配置已有未保存修改，请先保存或关闭原文件，再生成草稿。');
  try {
    const stat = await vscode.workspace.fs.stat(target);
    if (stat.size > 1024 * 1024) throw new BalanceError('目标配置超过 1 MB，未生成覆盖内容。');
    const bytes = await vscode.workspace.fs.readFile(target);
    const text = new TextDecoder('utf-8', {fatal: true}).decode(bytes);
    if (text.includes('\0')) throw new Error('not text');
    return text;
  } catch (error) {
    if (error instanceof vscode.FileSystemError && error.code === 'FileNotFound') return '';
    if (error instanceof BalanceError) throw error;
    throw new BalanceError('无法读取目标配置，请检查文件权限和 UTF-8 格式。');
  }
}
export class DraftEditor implements vscode.Disposable {
  private readonly drafts = new Map<string, {document: vscode.TextDocument; draft: Draft}>();
  private saving = false;
  private readonly status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  private readonly subscriptions: vscode.Disposable[];
  constructor() {
    this.status.name = '客户端配置草稿'; this.status.command = SAVE;
    this.subscriptions = [this.status, vscode.window.onDidChangeActiveTextEditor(() => this.update()),
      vscode.workspace.onDidCloseTextDocument(document => {this.drafts.delete(document.uri.toString()); this.update();}),
      vscode.languages.registerCodeLensProvider({scheme: 'untitled'}, {provideCodeLenses: document => {
        const entry = this.drafts.get(document.uri.toString());
        return entry ? [new vscode.CodeLens(new vscode.Range(0, 0, 0, 0), {title: entry.draft.label + ' · 保存到 ' + entry.draft.target.fsPath,
          command: SAVE, arguments: [document.uri]})] : [];
      }})];
    this.update();
  }
  private update(): void {
    const entry = vscode.window.activeTextEditor && this.drafts.get(vscode.window.activeTextEditor.document.uri.toString());
    void vscode.commands.executeCommand('setContext', 'newApiCockpitBalance.configDraft', !!entry);
    if (!entry) {this.status.hide(); return;}
    this.status.text = '$(save) ' + entry.draft.label; this.status.tooltip = '手动保存到 ' + entry.draft.target.fsPath; this.status.show();
  }
  ensureAvailable(targets: readonly vscode.Uri[]): void {
    if (targets.some(target => [...this.drafts.values()].some(entry => !entry.document.isClosed && entry.draft.target.fsPath === target.fsPath))) {
      throw new BalanceError('这个配置已有未保存草稿，请先保存或关闭旧草稿；不会覆盖你尚未保存的编辑。');
    }
  }
  async open(drafts: readonly Draft[]): Promise<vscode.TextDocument[]> {
    this.ensureAvailable(drafts.map(d => d.target));
    const documents: vscode.TextDocument[] = [], languages = await vscode.languages.getLanguages();
    for (const [index, draft] of drafts.entries()) {
      // A path-associated untitled document cannot point to an existing file in
      // the public VS Code API. Use ordinary untitled documents, and a Save
      // command/keybinding scoped exclusively to these drafts instead.
      const document = await vscode.workspace.openTextDocument({content: draft.content, language: languages.includes(draft.language) ? draft.language : 'plaintext'});
      this.drafts.set(document.uri.toString(), {document, draft});
      await vscode.window.showTextDocument(document, {preview: false, viewColumn: index ? vscode.ViewColumn.Beside : vscode.ViewColumn.Active});
      documents.push(document);
    }
    this.update(); return documents;
  }
  async save(uri = vscode.window.activeTextEditor?.document.uri): Promise<void> {
    const entry = uri && this.drafts.get(uri.toString());
    if (!entry || entry.document.isClosed || this.saving) return;
    this.saving = true;
    try {
      const {document, draft} = entry, text = document.getText(), version = document.version;
      validateDraft(text, draft.language);
      // Compare the original snapshot immediately before a user-requested save;
      // never silently replace someone else's intervening edit or dirty buffer.
      if (await read(draft.target) !== draft.source) throw new BalanceError('原配置在生成草稿后已变化，未覆盖；请关闭此草稿后重新生成。');
      const destination = await fs.realpath(draft.target.fsPath).catch((error: NodeJS.ErrnoException) => {
        if (error.code === 'ENOENT') return draft.target.fsPath;
        throw error;
      });
      await fs.mkdir(path.dirname(destination), {recursive: true, mode: 0o700});
      const temporary = path.join(path.dirname(destination), '.cockpit-config-' + randomUUID());
      try {
        const mode = draft.sensitive ? 0o600 : await fs.stat(destination).then(stat => stat.mode & 0o777, () => 0o600);
        const file = await fs.open(temporary, 'wx', mode);
        try {await file.writeFile(text, 'utf8'); await file.sync();} finally {await file.close();}
        await fs.rename(temporary, destination);
      } finally {await fs.rm(temporary, {force: true}).catch(() => undefined);}
      draft.source = text;
      if (document.version === version) {
        this.drafts.delete(document.uri.toString());
        await vscode.window.showTextDocument(document);
        await vscode.commands.executeCommand('workbench.action.revertAndCloseActiveEditor');
        await vscode.window.showTextDocument(draft.target, {preview: false});
      }
      void vscode.window.showInformationMessage(draft.label + ' 已保存。');
    } catch (error) {
      if (error instanceof BalanceError) throw error;
      throw new BalanceError('无法保存配置，请检查目标目录权限；草稿保留，未自动重试。');
    } finally {this.saving = false; this.update();}
  }
  dispose(): void {
    this.drafts.clear();
    void vscode.commands.executeCommand('setContext', 'newApiCockpitBalance.configDraft', false);
    for (const subscription of this.subscriptions) subscription.dispose();
  }
}
function validateDraft(text: string, language: string): void {
  // Validate, but do not regenerate or undo the user's edits at save time.
  if (language === 'toml') {codexConfig(text, 'https://validation.example.com'); return;}
  if (language === 'json') {
    try {const value: unknown = JSON.parse(text); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();}
    catch {throw new BalanceError('JSON 草稿格式有误，未保存。');}
    codexAuth(text, 'sk-draft_validation_placeholder_only');
  } else claudeConfig(text, 'https://validation.example.com', 'sk-draft_validation_placeholder_only');
}
async function claudeTarget(context: vscode.ExtensionContext): Promise<vscode.Uri | undefined> {
  const storage = context.globalStorageUri;
  // Desktop VS Code wraps local user data in vscode-userdata: URIs. Its own
  // FileUserDataProvider maps them to disk by changing only the scheme. Do not
  // reject this normal local URI or convert arbitrary remote/provider schemes.
  if (!['file', 'vscode-userdata'].includes(storage.scheme) || storage.scheme === 'vscode-userdata' && storage.authority) {
    throw new BalanceError('当前环境无法确定本机 VS Code 用户配置位置。');
  }
  const user = vscode.Uri.joinPath(storage.with({scheme: 'file'}), '..', '..');
  const targets = [{label: '默认用户配置', description: vscode.Uri.joinPath(user, 'settings.json').fsPath,
    target: vscode.Uri.joinPath(user, 'settings.json')}];
  try {
    for (const [name, type] of await vscode.workspace.fs.readDirectory(vscode.Uri.joinPath(user, 'profiles'))) {
      if (type & vscode.FileType.Directory) {
        const target = vscode.Uri.joinPath(user, 'profiles', name, 'settings.json');
        targets.push({label: '用户配置 ' + name, description: target.fsPath, target});
      }
    }
  } catch (error) {
    if (!(error instanceof vscode.FileSystemError && error.code === 'FileNotFound')) throw new BalanceError('无法读取 VS Code 用户配置目录。');
  }
  const choice = await vscode.window.showQuickPick([...targets, {label: '选择其他 settings.json…', description: '使用其他配置文件或自定义路径', target: undefined}],
    {title: 'Claude Code：选择 VS Code 用户配置文件', placeHolder: '不会使用项目的 .vscode/settings.json；请选择 Claude 扩展实际使用的用户配置'});
  if (!choice) return;
  if (choice.target) return choice.target;
  const selected = (await vscode.window.showOpenDialog({title: '选择 Claude Code 使用的 VS Code 用户 settings.json', defaultUri: targets[0].target,
    canSelectFolders: false, canSelectMany: false, filters: {'JSON 配置': ['json']}}))?.[0];
  if (selected && (selected.scheme !== 'file' || path.basename(selected.fsPath) !== 'settings.json' || path.basename(path.dirname(selected.fsPath)) === '.vscode')) {
    throw new BalanceError('请选择本机 VS Code 用户 settings.json，不使用项目配置文件。');
  }
  return selected;
}
export class ConfigDrafts {
  constructor(private readonly context: vscode.ExtensionContext, private readonly editor: DraftEditor) {}
  async prepare(kind: ClientKind, endpoint: string): Promise<((key: string) => Promise<void>) | undefined> {
    if (vscode.env.remoteName) throw new BalanceError('客户端配置草稿暂只支持本机窗口；请打开本地 VS Code 窗口，不会误写本机或远程配置。');
    if (kind === 'codex' && process.platform === 'win32' && vscode.workspace.getConfiguration('chatgpt').get<boolean>('runCodexInWindowsSubsystemForLinux')) {
      throw new BalanceError('Codex 当前在 WSL 内运行；不会用 Windows 主目录代替 WSL 配置目录。请先切换为本机 Codex 后再生成配置。');
    }
    let targets: vscode.Uri[], sources: string[];
    if (kind === 'claude') {
      const target = await claudeTarget(this.context); if (!target) return;
      targets = [target];
    } else {
      const home = codexHome(os.homedir(), process.env.CODEX_HOME);
      targets = ['config.toml', 'auth.json'].map(name => vscode.Uri.file(path.join(home, name)));
    }
    this.editor.ensureAvailable(targets);
    sources = await Promise.all(targets.map(read));
    const build = (key: string): Draft[] => kind === 'claude'
      ? [{target: targets[0], language: 'jsonc', content: claudeConfig(sources[0], endpoint, key), source: sources[0], label: 'Claude Code · settings.json', sensitive: true}]
      : [{target: targets[0], language: 'toml', content: codexConfig(sources[0], endpoint), source: sources[0], label: 'Codex · config.toml'},
        {target: targets[1], language: 'json', content: codexAuth(sources[1], key), source: sources[1], label: 'Codex · auth.json', sensitive: true}];
    // Fail on invalid config before revealing a real key or opening any drafts.
    build('sk-draft_validation_placeholder_only');
    return async key => {
      await this.editor.open(build(key));
      void vscode.window.showInformationMessage(kind === 'claude'
        ? 'Claude 配置草稿已打开。修改后手动保存，再重载 Claude 插件；未保存前原配置不变。草稿含完整 KEY，请勿分享。'
        : 'Codex 的两份配置草稿已打开。请分别保存 config.toml 和 auth.json，再重载 Codex；未保存前原配置不变。auth.json 含完整 KEY，请勿分享。');
    };
  }
}
