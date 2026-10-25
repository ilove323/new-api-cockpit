import * as vscode from 'vscode';
import * as path from 'node:path';
import { AccountController, AccountState } from './controller';
import { BalanceError, Connection, formatAmount, normalizeEndpoint, normalizePat } from './client';
import { RefreshGate } from './gate';
import { statusBarLabel } from './presentation';
import { KeysController } from './keys-controller';
import { KeysView, registerKeysCommands } from './keys-view';
import { ConfigDrafts, DraftEditor } from './config-drafts';

const PREFIX = 'newApiCockpitBalance', SECRET = 'account.connection.v1';
const time = (at: number) => new Date(at).toLocaleString();

class BalanceView implements vscode.TreeDataProvider<vscode.TreeItem>, vscode.Disposable {
  private readonly emitter = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.emitter.event;
  state: AccountState = {loading: false, nextAllowedAt: 0};
  update(state: AccountState): void { this.state = state; this.emitter.fire(); }
  getTreeItem(item: vscode.TreeItem): vscode.TreeItem { return item; }
  getChildren(): vscode.TreeItem[] {
    const s = this.state;
    if (!s.endpoint) return [];
    const row = (label: string, description: string, icon: string, tooltip?: string) => {
      const item = new vscode.TreeItem(label); item.description = description;
      item.iconPath = new vscode.ThemeIcon(icon); item.tooltip = tooltip || description; return item;
    };
    const rows: vscode.TreeItem[] = [];
    if (s.error) rows.push(row('未更新', s.error, 'warning'));
    if (s.balance) {
      const b = s.balance;
      rows.push(row('账户', b.user.username + ' (#' + b.user.id + ')', 'account'),
        row(s.error ? '上次余额' : '余额', formatAmount(b.remaining_amount), 'credit-card'),
        row('累计消费', formatAmount(b.used_amount), 'graph'),
        row('累计请求', b.request_count.toLocaleString(), 'list-ordered'),
        row('更新时间', time(s.updatedAt!), 'history'),
        row('金额单位', '账面单位', 'info', b.amount_unit));
    } else rows.push(row(s.loading ? '查询中' : '等待查询', s.loading ? '正在读取账户余额…' : '可使用右上角刷新按钮', s.loading ? 'loading~spin' : 'clock'));
    if (s.nextAllowedAt > Date.now()) rows.push(row('最早可刷新', time(s.nextAllowedAt), 'clock'));
    return rows;
  }
  dispose(): void { this.emitter.dispose(); }
}

function decodeConnection(value: string): Connection {
  const stored = JSON.parse(value);
  if (!stored || stored.version !== 1 || typeof stored.endpoint !== 'string' || typeof stored.pat !== 'string') {
    throw new Error('Invalid stored connection');
  }
  return {endpoint: normalizeEndpoint(stored.endpoint), pat: normalizePat(stored.pat)};
}

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const view = new BalanceView();
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 20);
  status.name = 'Cockpit 账户余额'; status.command = PREFIX + '.show';
  const tree = vscode.window.createTreeView(PREFIX + '.account', {treeDataProvider: view, showCollapseAll: false});
  const keysView = new KeysView();
  const keysTree = vscode.window.createTreeView(PREFIX + '.keys', {treeDataProvider: keysView});
  let disposed = false, configuring = false, loadVersion = 0;
  let activeSecret: string | null = null, interval: ReturnType<typeof setInterval> | undefined;
  const gate = new RefreshGate(path.join(context.globalStorageUri.fsPath, 'refresh'));
  const controller = new AccountController(gate, render);
  const keys = new KeysController(renderKeys);

  function renderKeys(): void {
    if (disposed) return;
    keysView.update(keys.state);
    const page = keys.state.page;
    keysTree.description = page ? '第 ' + page.page + ' 页 · 共 ' + page.total + ' 个' : '';
    void vscode.commands.executeCommand('setContext', PREFIX + '.keysBusy', keys.state.busy || keys.state.loading);
  }

  function render(): void {
    if (disposed) return;
    const s = controller.state, b = s.balance;
    view.update(s);
    tree.description = s.endpoint ? new URL(s.endpoint).host : undefined;
    void vscode.commands.executeCommand('setContext', PREFIX + '.connected', !!s.endpoint);
    void vscode.commands.executeCommand('setContext', PREFIX + '.loading', s.loading);
    const label = statusBarLabel(s);
    status.text = (s.loading ? '$(sync~spin) ' : s.error ? '$(warning) ' : '$(account) ') + label.text;
    const tooltip = new vscode.MarkdownString();
    tooltip.appendText(b ? b.user.username + (s.error ? '\n上次余额：' : '\n可用余额：') + formatAmount(b.remaining_amount) + '\n累计消费：' + formatAmount(b.used_amount) + '\n' + b.amount_unit + '\n更新时间：' + time(s.updatedAt!) : '点击配置账户连接或查看余额');
    if (s.error) tooltip.appendText('\n' + s.error);
    if (s.nextAllowedAt > Date.now()) tooltip.appendText('\n最早可刷新：' + time(s.nextAllowedAt));
    status.tooltip = tooltip;
    status.accessibilityInformation = {label: status.name + '，' + label.accessibleLabel}; status.show();
  }

  async function loadConnection(): Promise<void> {
    const version = ++loadVersion;
    try {
      const stored = await context.secrets.get(SECRET) || null;
      if (disposed || version !== loadVersion || stored === activeSecret) return;
      const connection = stored ? decodeConnection(stored) : undefined;
      activeSecret = stored; controller.setConnection(connection); keys.setConnection(connection);
      if (connection) await controller.refresh();
    } catch {
      if (disposed || version !== loadVersion) return;
      activeSecret = null; controller.setConnection(); keys.setConnection();
      void vscode.window.showErrorMessage('无法读取账户连接，请检查 VS Code 密钥库或重新配置。');
    }
  }

  async function connect(): Promise<void> {
    if (configuring) return;
    configuring = true;
    try {
      const site = await vscode.window.showInputBox({title: 'Cockpit 余额：站点地址',
        value: controller.state.endpoint ? new URL(controller.state.endpoint).origin : '',
        placeHolder: 'https://gateway.example.com', ignoreFocusOut: true,
        validateInput(value) { try { normalizeEndpoint(value); return undefined; } catch (error) { return (error as Error).message; } }});
      if (site === undefined || disposed) return;
      const pat = await vscode.window.showInputBox({title: 'Cockpit 余额：用户 API PAT', password: true, ignoreFocusOut: true,
        prompt: '余额需 profile:read；KEY 管理需 api_key:read、api_key:write；复制或配置客户端另需 api_key:reveal。请输入用户 API PAT。',
        validateInput(value) { try { normalizePat(value); return undefined; } catch (error) { return (error as Error).message; } }});
      if (pat === undefined || disposed) return;
      const connection = {version: 1, endpoint: normalizeEndpoint(site), pat: normalizePat(pat)};
      await context.secrets.store(SECRET, JSON.stringify(connection));
      await loadConnection();
      if (!disposed) await controller.refresh();
    } catch (error) {
      void vscode.window.showErrorMessage(error instanceof BalanceError ? error.message : '无法保存账户连接；请检查 VS Code 密钥库。');
    } finally { configuring = false; }
  }

  function schedule(): void {
    if (interval) clearInterval(interval);
    const setting = vscode.workspace.getConfiguration(PREFIX).get<number>('refreshIntervalMinutes', 15);
    const minutes = [0, 15, 30, 60].includes(setting) ? setting : 15;
    if (minutes) interval = setInterval(() => { if (!controller.state.authenticationFailed) void controller.refresh(); }, minutes * 60_000);
  }

  context.subscriptions.push(status, tree, view, keysView, keysTree,
    vscode.commands.registerCommand(PREFIX + '.connect', connect),
    vscode.commands.registerCommand(PREFIX + '.refresh', async () => {
      if (!controller.state.endpoint) { await connect(); return; }
      if (!controller.state.loading && controller.state.nextAllowedAt > Date.now()) {
        void vscode.window.showInformationMessage('请在 ' + time(controller.state.nextAllowedAt) + ' 后刷新。'); return;
      }
      await controller.refresh();
    }),
    vscode.commands.registerCommand(PREFIX + '.show', async () => {
      await vscode.commands.executeCommand(PREFIX + '.account.focus');
      if (!controller.state.endpoint) await connect();
    }),
    vscode.commands.registerCommand(PREFIX + '.disconnect', async () => {
      if (configuring) return;
      const expected = activeSecret;
      const answer = await vscode.window.showWarningMessage('删除本机保存的账户连接和 PAT？不会撤销服务器上的 PAT。', {modal: true}, '删除');
      if (answer !== '删除' || disposed) return;
      if (activeSecret !== expected || configuring) return;
      configuring = true;
      try { await context.secrets.delete(SECRET); await loadConnection(); }
      catch { void vscode.window.showErrorMessage('无法删除连接，请检查 VS Code 密钥库。'); }
      finally { configuring = false; }
    }),
    context.secrets.onDidChange(event => { if (event.key === SECRET) void loadConnection(); }),
    vscode.workspace.onDidChangeConfiguration(event => { if (event.affectsConfiguration(PREFIX + '.refreshIntervalMinutes')) schedule(); }),
    {dispose() { disposed = true; loadVersion++; controller.dispose(); keys.dispose(); activeSecret = null; if (interval) clearInterval(interval); }}
  );
  const draftEditor = new DraftEditor();
  context.subscriptions.push(draftEditor, vscode.commands.registerCommand(PREFIX + '.configSave', async (uri?: vscode.Uri) => {
    try {await draftEditor.save(uri);} catch (error) {void vscode.window.showErrorMessage(error instanceof BalanceError ? error.message : '配置保存未完成。');}
  }));
  const drafts = new ConfigDrafts(context, draftEditor);
  registerKeysCommands(context, keys, () => controller.state, async (kind, key, ticket) => {
    const endpoint = controller.state.endpoint;
    if (!endpoint || !keys.current(ticket)) throw new BalanceError('账户连接已变化，请重新操作。');
    const open = await drafts.prepare(kind, endpoint); if (!open) return;
    if (!keys.current(ticket)) throw new BalanceError('账户连接已变化，请重新操作。');
    await keys.copy(key, async secret => {
      if (!keys.current(ticket)) throw new BalanceError('账户连接已变化，请重新操作。');
      await open(secret);
    });
  });
  render(); renderKeys(); schedule(); await loadConnection();
}
