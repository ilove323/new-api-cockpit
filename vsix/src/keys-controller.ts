import { BalanceError, Connection } from './client';
import { CreateKey, GroupOption, KeyPage, KeysClient, KeyWriteError, ModelKey } from './keys-client';

export interface KeysState {
  connected: boolean; loading: boolean; busy: boolean; page?: KeyPage; updatedAt?: number;
  error?: string;
}
export interface OperationResult {applied: boolean; message: string}

export class KeysController {
  state: KeysState = {connected: false, loading: false, busy: false};
  private client?: KeysClient;
  private generation = 0;
  private abort = new AbortController();
  private pending?: Promise<void>;
  private pages = new Map<number, {page: KeyPage; at: number}>();
  private groupCache?: GroupOption[];
  private groupPending?: Promise<GroupOption[]>;
  private groupVersion = 0;
  // Full keys are retained only in this connection's memory, never in view state or files.
  private secrets = new Map<number, string>();
  constructor(private readonly changed: () => void,
    private readonly makeClient = (connection: Connection) => new KeysClient(connection), private readonly now = Date.now) {}

  setConnection(connection?: Connection): void {
    this.generation++; this.abort.abort(); this.abort = new AbortController();
    this.client = connection ? this.makeClient(connection) : undefined; this.pending = undefined; this.pages.clear(); this.clearPrivateCache();
    this.state = {connected: !!connection, loading: false, busy: false}; this.changed();
  }
  ticket(): number {return this.generation;}
  current(ticket: number): boolean {return this.state.connected && ticket === this.generation;}
  private clearPrivateCache(): void {this.groupVersion++; this.groupCache = undefined; this.groupPending = undefined; this.secrets.clear();}
  private report(error: unknown, generation: number): void {
    if (!this.current(generation)) return;
    this.state.error = error instanceof BalanceError ? error.message : 'KEY 查询暂不可用，请稍后再试。';
    if (error instanceof BalanceError && (error.status === 401 || error.status === 403 && error.requiredScope === 'api_key:read')) {
      this.state.page = undefined; this.pages.clear(); this.clearPrivateCache();
    }
    this.changed();
  }
  private async readPage(page: number, query = false, resetPrivateCache = false): Promise<KeyPage | undefined> {
    if (!this.client) return;
    const generation = this.generation, client = this.client, signal = this.abort.signal, cached = this.pages.get(page);
    if (!query && cached) {
      this.state.page = cached.page; this.state.updatedAt = cached.at; this.state.error = undefined; return cached.page;
    }
    const value = await client.list(page, signal); if (!this.current(generation)) return;
    // Explicit queries replace the snapshot, whereas first visits to another page
    // keep already cached pages available for local navigation.
    if (query) this.pages.clear();
    if (resetPrivateCache) this.clearPrivateCache();
    this.pages.set(page, {page: value, at: this.now()}); this.state.page = value; this.state.updatedAt = this.now();
    this.state.error = undefined; this.changed(); return value;
  }
  load(page = this.state.page?.page || 1, query = false): Promise<void> {
    if (!this.client || this.state.busy) return Promise.resolve();
    if (this.pending) return this.pending;
    const generation = this.generation; this.state.loading = true; this.changed();
    const pending = this.readPage(page, query, query).then(() => undefined).catch(error => this.report(error, generation)).finally(() => {
      if (this.current(generation)) {this.state.loading = false; this.changed();}
      if (this.pending === pending) this.pending = undefined;
    });
    this.pending = pending; return pending;
  }
  refresh(): Promise<void> {return this.load(this.state.page?.page || 1, true);}
  groups(): Promise<GroupOption[]> {
    if (!this.client) return Promise.reject(new BalanceError('请先配置账户连接。'));
    if (this.groupCache) return Promise.resolve(this.groupCache);
    if (this.groupPending) return this.groupPending;
    const generation = this.generation, version = this.groupVersion, client = this.client, signal = this.abort.signal;
    const pending = client.groups(signal).then(groups => {
      if (!this.current(generation) || version !== this.groupVersion) throw new BalanceError('账户或分组缓存已更新，请重新操作。');
      this.groupCache = groups; return groups;
    }).catch(error => {this.report(error, generation); throw error;})
      .finally(() => {if (this.groupPending === pending) this.groupPending = undefined;});
    this.groupPending = pending; return pending;
  }
  private selected(page: KeyPage, key: ModelKey): ModelKey {
    const latest = page.items.find(item => item.id === key.id);
    if (!latest) throw new BalanceError('本次查询中没有找到这个 KEY，请在插件内刷新列表。');
    return latest;
  }
  private async write(task: (client: KeysClient, signal: AbortSignal, page: KeyPage) => Promise<void>, message: string,
    verify?: (page: KeyPage) => boolean | undefined): Promise<OperationResult> {
    if (!this.client) return {applied: false, message: '请先配置账户连接。'};
    if (this.state.busy || this.state.loading) return {applied: false, message: '正在处理，请勿重复操作。'};
    const generation = this.generation, client = this.client, signal = this.abort.signal;
    this.state.busy = true; this.changed();
    try {
      const before = await this.readPage(this.state.page?.page || 1, true);
      if (!before || !this.current(generation)) return {applied: false, message: '账户连接已变化。'};
      let failure: unknown, failed = false;
      try {await task(client, signal, before);} catch (error) {failure = error; failed = true;}
      if (!this.current(generation)) return {applied: false, message: '账户连接已变化；切回原连接后在插件内刷新即可查看结果。'};
      // Read the result in the plugin. A lost acknowledgement must not create a
      // permanent lock or force a browser visit, and the mutation is never retried.
      let after: KeyPage | undefined;
      try {after = await this.readPage(1, true);} catch (error) {if (!failed) this.report(error, generation);}
      if (!this.current(generation)) return {applied: false, message: '账户连接已变化；切回原连接后在插件内刷新即可查看结果。'};
      const verified = after ? verify?.(after) : undefined;
      if (failed) {
        if (failure instanceof KeyWriteError && failure.uncertain && verified === true) {
          this.state.error = undefined; return {applied: true, message};
        }
        this.report(failure, generation); return {applied: false, message: this.state.error || '修改未完成。'};
      }
      if (verified === false) {
        this.state.error = 'New API 返回成功，但查询结果未显示修改生效。';
        return {applied: false, message: this.state.error};
      }
      return {applied: true, message: this.state.error ? message + '；列表未能更新，请手动查询核对。' : message};
    } catch (error) {
      this.report(error, generation);
      return {applied: false, message: this.current(generation) ? this.state.error || '修改未完成。' : '账户连接已变化；切回原连接后在插件内刷新即可查看结果。'};
    } finally {if (this.current(generation)) {this.state.busy = false; this.changed();}}
  }
  create(input: CreateKey): Promise<OperationResult> {
    const ticket = this.ticket();
    return this.write(async (client, signal) => {
      const groups = await this.groups(); if (!this.current(ticket)) throw new BalanceError('账户连接已变化。');
      await client.create(input, signal, groups);
    }, 'KEY 已申请');
  }
  remove(key: ModelKey): Promise<OperationResult> {
    const ticket = this.ticket();
    return this.write(async (client, signal, page) => {
      if (page.items.some(item => item.id === key.id)) await client.remove(key.id, signal);
      else if (page.page !== 1 || page.items.length !== page.total) this.selected(page, key);
      if (this.current(ticket)) this.secrets.delete(key.id);
    }, 'KEY 已删除', page => {
      if (page.items.some(item => item.id === key.id)) return false;
      return page.page === 1 && page.items.length === page.total ? true : undefined;
    }).then(result => {if (result.applied && this.current(ticket)) this.secrets.delete(key.id); return result;});
  }
  changeGroup(key: ModelKey, group: string): Promise<OperationResult> {
    const ticket = this.ticket();
    return this.write(async (client, signal, page) => {
      const groups = await this.groups(); if (!this.current(ticket)) throw new BalanceError('账户连接已变化。');
      await client.changeGroup(this.selected(page, key), group, signal, groups);
    }, 'KEY 分组已修改', page => {const item = page.items.find(item => item.id === key.id); return item ? item.group === group : undefined;});
  }
  async copy(key: ModelKey, consume: (key: string) => PromiseLike<unknown>): Promise<void> {
    if (!this.client || this.state.busy || this.state.loading) throw new BalanceError('请等待当前操作结束。');
    const generation = this.generation, client = this.client, signal = this.abort.signal;
    this.state.busy = true; this.changed();
    try {
      const cached = this.secrets.get(key.id);
      if (cached) {await consume(cached); if (this.current(generation)) this.state.error = undefined; return;}
      const secret = await client.reveal(key.id, signal);
      if (this.current(generation)) {this.secrets.set(key.id, secret); await consume(secret); this.state.error = undefined;}
    } catch (error) {this.report(error, generation); throw error;}
    finally {if (this.current(generation)) {this.state.busy = false; this.changed();}}
  }
  dispose(): void {this.generation++; this.abort.abort(); this.client = undefined; this.pages.clear(); this.clearPrivateCache();}
}
