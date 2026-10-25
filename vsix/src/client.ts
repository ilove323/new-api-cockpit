import { randomUUID } from 'node:crypto';

export interface Connection { endpoint: string; pat: string }
export interface Balance {
  user: { id: number; username: string };
  remaining_amount: string;
  used_amount: string;
  request_count: number;
  quota_per_unit: number;
  amount_unit: string;
}

export class BalanceError extends Error {
  constructor(message: string, readonly status = 0, readonly retryAt = 0, readonly requiredScope?: string) { super(message); }
}

export function normalizeEndpoint(input: string): string {
  let url: URL;
  try { url = new URL(input.trim()); } catch { throw new BalanceError('请填写完整站点地址，例如 https://gateway.example.com。'); }
  if (url.username || url.password || url.search || url.hash || !url.hostname) {
    throw new BalanceError('地址中不能包含账号、密码、查询参数或锚点。');
  }
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if (url.protocol !== 'https:' && !(url.protocol === 'http:' && local)) {
    throw new BalanceError('请使用 HTTPS；HTTP 仅允许本机回环地址。');
  }
  if (!['', '/', '/cockpit', '/cockpit/', '/cockpit/mcp', '/cockpit/mcp/'].includes(url.pathname)) {
    throw new BalanceError('请填写站点首页地址或 /cockpit/mcp 地址，不是模型 API 地址。');
  }
  return url.origin + '/cockpit/mcp';
}

export function normalizePat(input: string): string {
  const value = input.trim();
  if (!/^[A-Za-z0-9_+/=-]{16,256}$/.test(value) || value.startsWith('sk-')) {
    throw new BalanceError('请填写用户 API PAT，不是模型 sk- KEY、登录 JWT 或账号密码。');
  }
  return value;
}

export function retryAfter(value: string | undefined, now = Date.now()): number {
  if (!value) return now + 15 * 60_000;
  const seconds = /^\d+$/.test(value) ? Number(value) : NaN;
  const date = Number.isFinite(seconds) ? now + seconds * 1000 : Date.parse(value);
  return Number.isSafeInteger(date) ? Math.max(now + 15 * 60_000, date) : now + 15 * 60_000;
}

function httpError(status: number, retry: string | undefined, scope: string): BalanceError {
  if (status === 401) return new BalanceError('用户 API PAT 无效、已过期或已撤销，请重新配置连接。', status);
  if (status === 403) return new BalanceError('PAT 需要 ' + scope + ' 权限；请同时检查账号状态。', status, 0, scope);
  if (status === 429) return new BalanceError('请求过于频繁（HTTP 429），请稍后再试。', status, retryAfter(retry));
  if (status >= 300 && status < 400) return new BalanceError('站点返回重定向，请填写最终的 HTTPS 地址。', status);
  if (status === 404) return new BalanceError(scope.startsWith('api_key:') ? 'KEY 接口或记录不存在，请检查 New API 版本并刷新列表。' : '未找到账户查询服务，请检查地址和 Cockpit 的 MCP 开关。', status);
  return new BalanceError('账户查询暂不可用（HTTP ' + status + '）。', status);
}

export function connectionError(error: unknown): BalanceError {
  // Node fetch wraps the transport error in cause; never display raw messages,
  // URLs or proxy credentials from either layer.
  let source = error;
  for (let depth = 0; depth < 4 && source && typeof source === 'object' && !('code' in source) && 'cause' in source; depth++) source = source.cause;
  const raw = source && typeof source === 'object' && 'code' in source ? source.code : undefined;
  const code = typeof raw === 'string' && /^[A-Z][A-Z0-9_]{1,63}$/.test(raw) ? raw : 'NETWORK_ERROR';
  if (['CERT_HAS_EXPIRED', 'ERR_TLS_CERT_ALTNAME_INVALID', 'DEPTH_ZERO_SELF_SIGNED_CERT', 'SELF_SIGNED_CERT_IN_CHAIN',
    'UNABLE_TO_VERIFY_LEAF_SIGNATURE', 'UNABLE_TO_GET_ISSUER_CERT', 'UNABLE_TO_GET_ISSUER_CERT_LOCALLY'].includes(code)) {
    return new BalanceError('HTTPS 证书校验失败（' + code + '），没有关闭证书验证。');
  }
  if (['ECONNRESET', 'EPIPE', 'UND_ERR_SOCKET'].includes(code)) return new BalanceError('连接被中途断开（' + code + '），没有收到完整服务器响应。');
  if (code === 'ENOTFOUND' || code === 'EAI_AGAIN') return new BalanceError('站点域名解析失败（' + code + '）。');
  if (code === 'ECONNREFUSED') return new BalanceError('站点拒绝连接（ECONNREFUSED）。');
  if (['ETIMEDOUT', 'ESOCKETTIMEDOUT', 'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_HEADERS_TIMEOUT', 'UND_ERR_BODY_TIMEOUT'].includes(code)) return new BalanceError('连接超时（' + code + '）。');
  return new BalanceError('网络请求失败（' + code + '）。');
}

export interface OwnRequest {
  method: 'GET' | 'POST' | 'PUT' | 'DELETE'; path: string; body?: unknown;
  scope: string; signal: AbortSignal; timeout?: number;
}

export async function requestOwnJson(connection: Connection, options: OwnRequest): Promise<unknown> {
  const endpoint = normalizeEndpoint(connection.endpoint), pat = normalizePat(connection.pat);
  const {method, path, signal, scope, timeout = 10_000} = options;
  const allowed = method === 'POST' && path === '/cockpit/mcp'
    || method === 'GET' && /^\/api\/token\/\?p=[1-9]\d{0,3}&page_size=50$/.test(path)
    || method === 'GET' && /^\/api\/token\/[1-9]\d{0,14}$/.test(path)
    || method === 'GET' && path === '/api/user/self/groups'
    || (method === 'POST' || method === 'PUT') && path === '/api/token/'
    || method === 'DELETE' && /^\/api\/token\/[1-9]\d{0,14}$/.test(path)
    || method === 'POST' && /^\/api\/token\/[1-9]\d{0,14}\/key$/.test(path);
  if (!allowed) throw new BalanceError('不允许的账户请求。');
  if (signal.aborted) throw new BalanceError('查询已取消。');
  const url = new URL(path, new URL(endpoint).origin), abort = new AbortController();
  let timedOut = false;
  const cancel = () => abort.abort(); signal.addEventListener('abort', cancel, {once: true});
  const timer = setTimeout(() => {timedOut = true; abort.abort();}, timeout);
  try {
    // Use the host's fetch transport and connection pool. VS Code's HTTP proxy
    // patch replaces https.request agents, so a custom keep-alive Agent alone
    // does not reliably reuse connections inside the real extension host.
    const response = await fetch(url, {
      method, signal: abort.signal, redirect: 'manual', credentials: 'omit',
      headers: {
        Authorization: 'Bearer ' + pat,
        'Cache-Control': 'no-store',
        Accept: path === '/cockpit/mcp' ? 'application/json, text/event-stream' : 'application/json',
        ...(options.body === undefined ? {} : {'Content-Type': 'application/json'}),
        ...(path === '/cockpit/mcp' ? {'MCP-Protocol-Version': '2025-11-25'} : {}),
        'User-Agent': 'new-api-cockpit-vscode/' + (require('../package.json') as {version: string}).version
      },
      ...(options.body === undefined ? {} : {body: JSON.stringify(options.body)})
    });
    const responseError = response.status !== 200 ? httpError(response.status, response.headers.get('retry-after') || undefined, scope)
      : !response.headers.get('content-type')?.toLowerCase().startsWith('application/json') ? new BalanceError('账户服务没有返回 JSON，请检查站点地址。') : undefined;
    const chunks: Uint8Array[] = []; let bytes = 0;
    const reader = response.body?.getReader();
    if (reader) {
      try {
        for (;;) {
          const {done, value} = await reader.read(); if (done) break;
          bytes += value.byteLength;
          if (bytes > 256 * 1024) {
            await reader.cancel().catch(() => undefined);
            throw responseError || new BalanceError('账户响应过大，已停止读取。');
          }
          // Drain bounded error responses as well, so the transport can reuse
          // their connection without retaining or disclosing server content.
          if (!responseError) chunks.push(value);
        }
      } catch (error) {if (responseError) throw responseError; throw error;}
      finally {reader.releaseLock();}
    }
    if (responseError) throw responseError;
    try {return JSON.parse(Buffer.concat(chunks).toString('utf8'));}
    catch {throw new BalanceError('账户响应格式无效。');}
  } catch (error) {
    if (error instanceof BalanceError) throw error;
    if (signal.aborted) throw new BalanceError('查询已取消。');
    if (timedOut) throw new BalanceError('账户请求超时，请稍后刷新。');
    throw connectionError(error);
  } finally {clearTimeout(timer); signal.removeEventListener('abort', cancel);}
}

const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const amount = (value: unknown): value is string => typeof value === 'string' && /^-?\d{1,32}(?:\.\d{1,12})?$/.test(value);

export async function fetchBalance(connection: Connection, signal: AbortSignal, timeout = 10_000): Promise<Balance> {
  // Dedicated client for Cockpit's stateless endpoint: one read-only tool call,
  // no model API, credential lookup, initialize, tool discovery or retry.
  const verified = {endpoint: normalizeEndpoint(connection.endpoint), pat: normalizePat(connection.pat)};
  const id = randomUUID();
  const value = await requestOwnJson(verified, {path: '/cockpit/mcp', method: 'POST', scope: 'profile:read', signal, timeout,
    body: {jsonrpc: '2.0', id, method: 'tools/call', params: {name: 'get_balance', arguments: {}}}});
  if (!record(value) || value.jsonrpc !== '2.0' || value.id !== id || value.error || !record(value.result)) {
    throw new BalanceError('账户查询协议响应无效，请检查 Cockpit 版本。');
  }
  if (value.result.isError) throw new BalanceError('账户余额暂不可查询，请稍后刷新。');
  const data = value.result.structuredContent;
  if (!record(data) || !record(data.user) || !integer(data.user.id) || data.user.id < 1 ||
      typeof data.user.username !== 'string' || !data.user.username || data.user.username.length > 128 || /[\u0000-\u001f\u007f]/.test(data.user.username) ||
      !amount(data.remaining_amount) || !amount(data.used_amount) || !integer(data.request_count) ||
      !integer(data.quota_per_unit) || data.quota_per_unit < 1 || typeof data.amount_unit !== 'string' || data.amount_unit.length > 256) {
    throw new BalanceError('账户余额数据不完整，未显示为零。');
  }
  return {user: {id: data.user.id, username: data.user.username}, remaining_amount: data.remaining_amount,
    used_amount: data.used_amount, request_count: data.request_count, quota_per_unit: data.quota_per_unit, amount_unit: data.amount_unit};
}

export function formatAmount(value: string): string {
  const [whole, fraction = ''] = value.split('.');
  const decimals = fraction.replace(/0+$/, '').padEnd(2, '0');
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + decimals;
}
