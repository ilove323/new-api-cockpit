import { BalanceError, Connection, requestOwnJson } from './client';

export interface ModelKey {
  id: number; name: string; group: string; masked: string; status: number;
  expired_time: number; unlimited_quota: boolean; remain_quota: number;
}
export interface KeyPage {items: ModelKey[]; total: number; page: number}
export interface GroupOption {name: string; description: string}
export interface CreateKey {name: string; group: string; unlimited_quota: boolean; remain_quota: number; expired_time: number}
export class KeyWriteError extends BalanceError {
  constructor(message: string, readonly uncertain: boolean, status = 0, retryAt = 0) {super(message, status, retryAt, 'api_key:write');}
}
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value);
const text = (value: unknown, max = 256): value is string => typeof value === 'string' && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value);
function ident(id: number): number {if (!integer(id) || id < 1 || id > 999999999999999) throw new BalanceError('KEY 标识无效。'); return id;}
function success(value: unknown): Record<string, unknown> {
  if (!object(value) || value.success !== true) throw new BalanceError('KEY 操作未成功，请检查 PAT 权限、分组、额度或 KEY 数量限制。');
  return value;
}
function readKey(raw: unknown): ModelKey {
  if (!object(raw) || !integer(raw.id) || !integer(raw.user_id) || raw.user_id < 1 || !text(raw.name) || !text(raw.group) ||
      !integer(raw.status) || !integer(raw.expired_time) || typeof raw.unlimited_quota !== 'boolean' || !integer(raw.remain_quota)) {
    throw new BalanceError('KEY 列表响应不完整。');
  }
  const key = typeof raw.key === 'string' ? raw.key.replace(/^sk-/, '') : '';
  return {id: ident(raw.id), name: raw.name, group: raw.group, masked: key ? key.length <= 8 ? 'sk-••••' : 'sk-' + key.slice(0, 4) + '••••' + key.slice(-4) : '未显示',
    status: raw.status, expired_time: raw.expired_time, unlimited_quota: raw.unlimited_quota, remain_quota: raw.remain_quota};
}
export function validateName(input: string): string {
  const value = input.trim();
  if (!text(value, 50) || !value || Buffer.byteLength(value) > 50) throw new BalanceError('请填写较短的 KEY 名称（最多 50 字节）。');
  return value;
}
export function amountToQuota(input: string, unit: number): number {
  if (!/^\d{1,16}(?:\.\d{1,6})?$/.test(input.trim()) || !integer(unit) || unit < 1) throw new BalanceError('请输入有效的正数额度，最多六位小数。');
  const [whole, fraction = ''] = input.trim().split('.'), denominator = 10n ** BigInt(fraction.length);
  const numerator = (BigInt(whole) * denominator + BigInt(fraction || '0')) * BigInt(unit);
  if (numerator % denominator || numerator <= 0n || numerator / denominator > BigInt(Number.MAX_SAFE_INTEGER)) throw new BalanceError('额度过大、过小或精度不符合站点要求。');
  return Number(numerator / denominator);
}

export class KeysClient {
  constructor(private readonly connection: Connection) {}
  private request(path: string, method: 'GET' | 'POST' | 'PATCH' | 'DELETE', scope: string, signal: AbortSignal, body?: unknown): Promise<unknown> {
    return requestOwnJson(this.connection, {path, method, scope, signal, body});
  }
  private async write(path: string, method: 'POST' | 'PATCH' | 'DELETE', signal: AbortSignal, body?: unknown): Promise<void> {
    try {
      const value = await this.request(path, method, 'api_key:write', signal, body);
      if (object(value) && value.success === false) throw new KeyWriteError('KEY 修改未成功，请检查分组、额度、权限或数量限制。', false);
      if (!object(value) || value.success !== true) throw new KeyWriteError('New API 的修改响应格式无效，插件将查询实际结果。', true);
    } catch (error) {
      if (error instanceof KeyWriteError) throw error;
      if (error instanceof BalanceError && [400, 401, 403, 404, 405, 413, 429].includes(error.status)) throw new KeyWriteError(error.message, false, error.status, error.retryAt);
      throw new KeyWriteError(error instanceof BalanceError ? '修改回执未收到：' + error.message : '修改回执未收到，插件已查询最新列表；没有自动重发修改。', true);
    }
  }
  async list(page: number, signal: AbortSignal): Promise<KeyPage> {
    if (!integer(page) || page < 1 || page > 9999) throw new BalanceError('KEY 页码无效。');
    const value = success(await this.request('/api/token/?p=' + page + '&page_size=50', 'GET', 'api_key:read', signal)), data = value.data;
    if (!object(data) || !Array.isArray(data.items) || data.items.length > 50 || !integer(data.total) || data.total < 0) throw new BalanceError('KEY 分页响应无效。');
    return {items: data.items.map(readKey), total: data.total, page};
  }
  async groups(signal: AbortSignal): Promise<GroupOption[]> {
    const value = success(await this.request('/api/user/self/groups', 'GET', 'profile:read', signal));
    if (!object(value.data) || Object.keys(value.data).length > 256) throw new BalanceError('分组响应无效。');
    const groups: GroupOption[] = [{name: '', description: '默认分组（跟随账户）'}];
    for (const [name, info] of Object.entries(value.data)) {
      if (!text(name) || !name || !object(info)) throw new BalanceError('分组响应无效。');
      groups.push({name, description: text(info.desc) ? info.desc : ''});
    }
    return groups;
  }
  async create(input: CreateKey, signal: AbortSignal, groups: readonly GroupOption[]): Promise<void> {
    const name = validateName(input.name);
    if (!text(input.group) || typeof input.unlimited_quota !== 'boolean' || !integer(input.remain_quota) || input.remain_quota < 0 ||
        !input.unlimited_quota && input.remain_quota <= 0 || !integer(input.expired_time) || input.expired_time !== -1 && input.expired_time <= Date.now() / 1000) throw new BalanceError('KEY 参数无效。');
    if (!groups.some(g => g.name === input.group)) throw new BalanceError('所选分组不可用，请刷新后重新选择。');
    await this.write('/api/token/', 'POST', signal, {name, group: input.group,
      remain_quota: input.unlimited_quota ? 0 : input.remain_quota, unlimited_quota: input.unlimited_quota, expired_time: input.expired_time,
      model_limits_enabled: false, model_limits: '', allow_ips: '', cross_group_retry: false, auto_groups: null});
  }
  async remove(id: number, signal: AbortSignal): Promise<void> {
    await this.write('/api/token/' + ident(id), 'DELETE', signal);
  }
  async changeGroup(key: ModelKey, group: string, signal: AbortSignal, groups: readonly GroupOption[]): Promise<void> {
    if (!text(group)) throw new BalanceError('分组参数无效。');
    if (!groups.some(g => g.name === group)) throw new BalanceError('所选分组不可用，请刷新后重新选择。');
    if (key.group === group) return;
    // PATCH is intentionally a separate method/path: an unmodified server
    // rejects it instead of treating missing quota fields as a full PUT.
    try {
      await this.write('/api/token/' + ident(key.id), 'PATCH', signal, {group, cross_group_retry: false, auto_groups: null});
    } catch (error) {
      if (error instanceof KeyWriteError && [404, 405].includes(error.status)) {
        throw new KeyWriteError('KEY 已不可用或 New API 尚未支持安全改组接口；需要配套服务端更新，没有改用旧接口重试。', false, error.status);
      }
      throw error;
    }
  }
  async reveal(id: number, signal: AbortSignal): Promise<string> {
    const result = success(await this.request('/api/token/' + ident(id) + '/key', 'POST', 'api_key:reveal', signal, {}));
    const key = object(result.data) && typeof result.data.key === 'string' ? result.data.key.replace(/^sk-/, '') : '';
    if (!/^[A-Za-z0-9_-]{16,256}$/.test(key) || key.startsWith('sk-')) throw new BalanceError('未取得有效的模型 KEY。');
    return 'sk-' + key;
  }
}
