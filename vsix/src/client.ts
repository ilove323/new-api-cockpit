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
    throw new BalanceError('请填写 New API 站点首页地址，不是模型 API 地址。');
  }
  // Accept saved Cockpit addresses on upgrade, but never call their MCP path.
  return url.origin;
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
  if (scope === 'public' && [401, 403].includes(status)) return new BalanceError('New API 公开状态接口被拒绝，无法读取额度单位。', status);
  if (status === 401) return new BalanceError('用户 API PAT 无效、已过期或已撤销，请重新配置连接。', status);
  if (status === 403) return new BalanceError('PAT 需要 ' + scope + ' 权限；请同时检查账号状态。', status, 0, scope);
  if (status === 429) return new BalanceError('请求过于频繁（HTTP 429），请稍后再试。', status, retryAfter(retry));
  if (status >= 300 && status < 400) return new BalanceError('站点返回重定向，请填写最终的 HTTPS 地址。', status);
  if (status === 404) return new BalanceError(scope.startsWith('api_key:') ? 'KEY 接口或记录不存在，请检查 New API 版本并刷新列表。' : '未找到 New API 账户接口，请检查站点地址。', status);
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
  const allowed = method === 'GET' && (path === '/api/user/self' || path === '/api/status')
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
        ...(path === '/api/status' ? {} : {Authorization: 'Bearer ' + pat}),
        'Cache-Control': 'no-store',
        Accept: 'application/json',
        ...(options.body === undefined ? {} : {'Content-Type': 'application/json'}),
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
const signedInteger = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value);

function quotaAmount(quota: number, unit: number): string {
  // Divide integer quota exactly; do not round through floating-point money.
  const value = BigInt(quota), divisor = BigInt(unit), absolute = value < 0n ? -value : value;
  const whole = absolute / divisor;
  let remainder = absolute % divisor, fraction = '';
  while (remainder && fraction.length < 64) {
    remainder *= 10n; fraction += (remainder / divisor).toString(); remainder %= divisor;
  }
  if (remainder) throw new BalanceError('站点额度单位无法精确换算，未显示为零。');
  return (value < 0n ? '-' : '') + whole.toString() + (fraction ? '.' + fraction : '');
}

export async function fetchBalance(connection: Connection, signal: AbortSignal, timeout = 10_000, knownQuotaPerUnit?: number): Promise<Balance> {
  const verified = {endpoint: normalizeEndpoint(connection.endpoint), pat: normalizePat(connection.pat)};
  const deadline = Date.now() + timeout;
  const value = await requestOwnJson(verified, {path: '/api/user/self', method: 'GET', scope: 'profile:read', signal, timeout});
  const data = record(value) && value.success === true ? value.data : undefined;
  if (!record(data) || !integer(data.id) || data.id < 1 ||
      typeof data.username !== 'string' || !data.username || data.username.length > 128 || /[\u0000-\u001f\u007f]/.test(data.username) ||
      !signedInteger(data.quota) || !signedInteger(data.used_quota) || !integer(data.request_count) || !integer(data.status)) {
    throw new BalanceError('账户余额数据不完整，未显示为零。');
  }
  if (data.status !== 1) throw new BalanceError('账户已禁用，请检查账户状态。', 403);
  let unit = knownQuotaPerUnit;
  if (unit === undefined) {
    const status = await requestOwnJson(verified, {path: '/api/status', method: 'GET', scope: 'public', signal, timeout: Math.max(1, deadline - Date.now())});
    unit = record(status) && status.success === true && record(status.data) ? status.data.quota_per_unit as number : undefined;
  }
  if (!integer(unit) || unit < 1) throw new BalanceError('New API 未返回有效的额度单位，未显示为零。');
  return {user: {id: data.id, username: data.username}, remaining_amount: quotaAmount(data.quota, unit),
    used_amount: quotaAmount(data.used_quota, unit), request_count: data.request_count, quota_per_unit: unit,
    amount_unit: '账面单位（quota / ' + unit + '），未做货币或汇率转换'};
}

export function formatAmount(value: string): string {
  const [whole, fraction = ''] = value.split('.');
  const decimals = fraction.replace(/0+$/, '').padEnd(2, '0');
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + decimals;
}
