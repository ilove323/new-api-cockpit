import * as vscode from 'vscode';
import { AccountState } from './controller';
import { BalanceError } from './client';
import { KeysController, KeysState, OperationResult } from './keys-controller';
import { amountToQuota, ModelKey, validateName } from './keys-client';
import type { ClientKind } from './client-config';

const PREFIX = 'newApiCockpitBalance';
class KeyNode extends vscode.TreeItem {kind?: 'group'; group?: string; key?: ModelKey;}
export class KeysView implements vscode.TreeDataProvider<KeyNode>, vscode.Disposable {
  private readonly emitter = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this.emitter.event;
  state: KeysState = {connected: false, loading: false, busy: false};
  update(state: KeysState): void {this.state = state; this.emitter.fire();}
  getTreeItem(item: KeyNode): KeyNode {return item;}
  getChildren(parent?: KeyNode): KeyNode[] {
    const s = this.state;
    if (!s.connected) return [];
    if (parent?.kind === 'group') return (s.page?.items || []).filter(k => k.group === parent.group).map(key => {
      const item = new KeyNode(key.name || '(未命名 KEY)'); item.key = key; item.id = 'key-' + key.id; item.contextValue = 'cockpitKey';
      item.iconPath = new vscode.ThemeIcon('key'); item.description = key.masked;
      item.tooltip = 'ID：' + key.id + '\n分组：' + (key.group || '默认分组') + '\n' + (key.status === 1 ? '已启用' : '未启用')
        + '\n到期：' + (key.expired_time === -1 ? '长期有效' : new Date(key.expired_time * 1000).toLocaleString()); return item;
    });
    const rows: KeyNode[] = [];
    const action = (label: string, command?: string, args?: unknown[]) => {
      const item = new KeyNode(label); if (command) item.command = {command: PREFIX + '.' + command, title: label, arguments: args}; return item;
    };
    if (s.error) {const item = action(s.error); item.tooltip = s.error; item.iconPath = new vscode.ThemeIcon('warning'); rows.push(item);}
    if (!s.page) rows.push(action(s.loading ? '正在加载 KEY…' : '载入我的 KEY', s.loading ? undefined : 'keysRefresh'));
    else {
      for (const group of [...new Set(s.page.items.map(k => k.group))].sort((a, b) => a.localeCompare(b))) {
        const item = new KeyNode((group || '默认分组') + ' (' + s.page.items.filter(k => k.group === group).length + ')', vscode.TreeItemCollapsibleState.Expanded);
        item.kind = 'group'; item.group = group; item.id = 'group-' + encodeURIComponent(group); item.iconPath = new vscode.ThemeIcon('folder'); rows.push(item);
      }
      if (!s.page.total) rows.push(action('暂无 KEY，点击申请', 'keysCreate'));
      if (s.page.page > 1) rows.push(action('上一页', 'keysPage', [s.page.page - 1]));
      if (s.page.page * 50 < s.page.total) rows.push(action('下一页', 'keysPage', [s.page.page + 1]));
    }
    return rows;
  }
  dispose(): void {this.emitter.dispose();}
}

export function registerKeysCommands(context: vscode.ExtensionContext, controller: KeysController,
  account: () => AccountState, configure?: (kind: ClientKind, key: ModelKey, ticket: number) => Promise<void>): void {
  let prompting = false;
  const current = (ticket: number) => {
    if (!controller.current(ticket)) throw new BalanceError('账户连接已变化，请重新操作。');
  };
  const register = (name: string, action: (argument?: unknown) => Promise<void>) => {
    context.subscriptions.push(vscode.commands.registerCommand(PREFIX + '.' + name, async (argument?: unknown) => {
      try {await action(argument);} catch (error) {void vscode.window.showErrorMessage(error instanceof BalanceError ? error.message : 'KEY 操作未完成，请稍后核对。');}
    }));
  };
  async function prompt(action: (ticket: number) => Promise<void>): Promise<void> {
    if (prompting || controller.state.busy || controller.state.loading) return;
    if (!controller.state.connected) {await vscode.commands.executeCommand(PREFIX + '.connect'); return;}
    prompting = true; const ticket = controller.ticket();
    try {await action(ticket);} finally {prompting = false;}
  }
  async function chooseGroup(ticket: number, selected = ''): Promise<string | undefined> {
    const groups = await controller.groups(); current(ticket);
    const choice = await vscode.window.showQuickPick(groups.map(g => ({label: g.name || '默认分组', description: g.description,
      picked: g.name === selected, name: g.name})), {title: '选择 KEY 分组', placeHolder: '只显示当前账户可用分组'});
    current(ticket); return choice?.name;
  }
  async function chooseKey(argument: unknown, ticket: number): Promise<ModelKey | undefined> {
    if (!controller.state.page) {await controller.load(); current(ticket);}
    const items = controller.state.page?.items || [];
    const candidate = argument instanceof KeyNode ? argument.key : undefined;
    if (candidate) return items.find(k => k.id === candidate.id);
    const result = await vscode.window.showQuickPick(items.map(key => ({label: key.name || '(未命名)', description: (key.group || '默认分组') + ' · ' + key.masked, key})), {title: '选择本人 KEY'});
    current(ticket); return result?.key;
  }
  function done(result: OperationResult, ticket: number): void {
    if (!controller.current(ticket)) return;
    if (result.applied) void vscode.window.showInformationMessage(result.message);
    else void vscode.window.showWarningMessage(result.message);
  }
  register('keysRefresh', async () => {await controller.refresh();});
  register('keysPage', async value => {if (typeof value === 'number' && Number.isSafeInteger(value) && value >= 1 && value <= 9999) await controller.load(value);});
  register('keysCreate', async () => prompt(async ticket => {
    const name = await vscode.window.showInputBox({title: '申请 KEY：名称', value: 'VS Code', ignoreFocusOut: true,
      validateInput(value) {try {validateName(value); return undefined;} catch (error) {return (error as Error).message;}}});
    if (name === undefined) return; current(ticket);
    const group = await chooseGroup(ticket); if (group === undefined) return;
    const quota = await vscode.window.showQuickPick([
      {label: '设置 KEY 额度', unlimited: false, description: '为这个 KEY 单独设置使用上限'},
      {label: '不单独限额', unlimited: true, description: '仍受本人账户余额限制'}
    ], {title: '申请 KEY：额度'}); if (!quota) return; current(ticket);
    let remain = 0, quotaDescription = '不单独限额（仍受账户余额限制）';
    if (!quota.unlimited) {
      const unit = account().balance?.quota_per_unit;
      if (!unit) throw new BalanceError('请先成功查询账户余额，确认额度单位后再申请有限额 KEY。');
      const amount = await vscode.window.showInputBox({title: 'KEY 额度（站点账面单位）', placeHolder: '例如 10', ignoreFocusOut: true,
        validateInput(value) {try {amountToQuota(value, unit); return undefined;} catch (error) {return (error as Error).message;}}});
      if (amount === undefined) return; current(ticket); remain = amountToQuota(amount, unit); quotaDescription = '额度 ' + amount.trim() + '（账面单位）';
    }
    const expiry = await vscode.window.showQuickPick([7, 30, 90, -1].map(days => ({label: days === -1 ? '长期有效' : days + ' 天', days})), {title: '申请 KEY：有效期'});
    if (!expiry) return; current(ticket);
    const answer = await vscode.window.showWarningMessage('申请 KEY“' + validateName(name) + '”？分组：' + (group || '默认分组') + '；' + quotaDescription + '；有效期：' + expiry.label + '。', {modal: true}, '申请');
    if (answer !== '申请') return; current(ticket);
    done(await controller.create({name, group, unlimited_quota: quota.unlimited, remain_quota: remain,
      expired_time: expiry.days === -1 ? -1 : Math.floor(Date.now() / 1000) + expiry.days * 86400}), ticket);
  }));
  register('keysDelete', async argument => prompt(async ticket => {
    const key = await chooseKey(argument, ticket); if (!key) return;
    const answer = await vscode.window.showWarningMessage('删除 KEY“' + key.name + '”（#' + key.id + '）？使用它的客户端将无法继续调用。', {modal: true}, '删除');
    if (answer !== '删除') return; current(ticket); done(await controller.remove(key), ticket);
  }));
  register('keysGroup', async argument => prompt(async ticket => {
    const key = await chooseKey(argument, ticket); if (!key) return;
    const group = await chooseGroup(ticket, key.group); if (group === undefined || group === key.group) return;
    current(ticket); done(await controller.changeGroup(key, group), ticket);
  }));
  register('keysCopy', async argument => prompt(async ticket => {
    const key = await chooseKey(argument, ticket); if (!key) return;
    await controller.copy(key, secret => vscode.env.clipboard.writeText(secret));
    if (controller.current(ticket)) void vscode.window.showInformationMessage('KEY 已复制到剪贴板。');
  }));
  register('keysConfigure', async argument => prompt(async ticket => {
    const key = await chooseKey(argument, ticket); if (!key) return;
    const choice = await vscode.window.showQuickPick([
      {label: 'Claude Code', description: 'VS Code 用户配置 · Messages 协议', client: 'claude' as const},
      {label: 'Codex', description: 'config.toml + auth.json · Responses 协议', client: 'codex' as const}
    ], {title: '选择需要配置的客户端', placeHolder: '打开未保存配置草稿，由你修改并保存'});
    if (!choice) return; current(ticket);
    if (configure) await configure(choice.client, key, ticket);
  }));
}
