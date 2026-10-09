/* Transport and examples shared by the documentation UI; no storage or retries. */
(() => {
  'use strict';
  const methods = new Set(['get', 'post', 'put', 'patch', 'delete']);
  function operations(spec) {
    return Object.entries(spec.paths).flatMap(([path, item]) => Object.entries(item)
      .filter(([method]) => methods.has(method))
      .map(([method, operation]) => ({...operation, method: method.toUpperCase(), path})));
  }
  function createPolicy(spec) {
    const routes = operations(spec).map(operation => ({operation,
      pattern: new RegExp('^' + operation.path.split(/(\{[^}]+\})/).map(part => part.startsWith('{') ? '[^/]+' : part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('') + '$')}));
    return (input, origin, method) => {
      const url = new URL(input, origin);
      if (url.origin !== origin || url.username || url.password || url.hash || !['http:', 'https:'].includes(url.protocol)) throw new Error('只能调用当前站点的接口。');
      const operation = routes.find(item => item.operation.method === method.toUpperCase() && item.pattern.test(url.pathname))?.operation;
      if (!operation || operation['x-cockpit-testable'] !== true) throw new Error('这个地址不支持在文档中执行；会话变更请使用正常登录入口。');
      return operation;
    };
  }
  function policy(spec, input, origin, method) {
    return createPolicy(spec)(input, origin, method);
  }
  function buildRequest(operation, values, body, origin) {
    let path = operation.path;
    const query = new URLSearchParams();
    for (const param of operation.parameters || []) {
      if (param.in === 'header') continue;
      const value = values[param.in + ':' + param.name];
      if (value === undefined || value === '') {
        if (param.required) throw new Error('请填写 ' + param.name + '。');
        continue;
      }
      if (param.in === 'path') {
        if (param.schema.type === 'integer' && (!/^\d+$/.test(String(value)) || Number(value) < param.schema.minimum || !Number.isSafeInteger(Number(value)))) throw new Error(param.name + ' 必须是有效的整数 ID。');
        if (String(value).includes('/') || String(value) === '.' || String(value) === '..') throw new Error('路径参数不合法。');
        path = path.replace('{' + param.name + '}', encodeURIComponent(value));
      } else {
        const list = param.schema.type === 'array' ? JSON.parse(value) : [value];
        if (!Array.isArray(list) || list.some(item => typeof item !== 'string' && typeof item !== 'number')) throw new Error(param.name + ' 请填写 JSON 数组。');
        for (const item of list) query.append(param.name, item);
      }
    }
    const url = new URL(path, origin);
    url.search = query.toString();
    const headers = {'Accept': 'application/json', ...operation['x-cockpit-confirm-headers']};
    const init = {method: operation.method, headers};
    if (operation.requestBody) {
      let parsed;
      try { parsed = JSON.parse(body); } catch { throw new Error('请求体不是有效的 JSON。'); }
      if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') throw new Error('请求体必须是 JSON 对象。');
      init.body = JSON.stringify(parsed);
      headers['Content-Type'] = 'application/json';
    }
    return {url: url.href, init};
  }
  function redact(value) {
    if (Array.isArray(value)) return value.map(redact);
    if (!value || typeof value !== 'object') return value;
    return Object.fromEntries(Object.entries(value).map(([key, data]) => [key, /password|secret|access_token|webhook_url|^key$/i.test(key) ? '（已隐藏）' : redact(data)]));
  }
  function createTransport({spec, origin, fetchOnce, authenticate, confirm}) {
    const checkPolicy = createPolicy(spec);
    let busy = false;
    return async request => {
      if (busy) throw new Error('上一请求尚未结束，请勿重复提交。');
      const operation = checkPolicy(request.url, origin, request.init.method);
      busy = true;
      try {
        const auth = authenticate();
        if (auth.mode === 'pat' && (!auth.token || /\s/.test(auth.token))) throw new Error('请填写有效的管理员 PAT 原值。');
        if (auth.mode !== 'pat' && operation['x-cockpit-pat-only']) throw new Error('这个接口只接受管理员 PAT，请切换调试身份并填写 PAT。');
        if (!['read', 'preview'].includes(operation['x-cockpit-effect'])) {
          const target = operation.method + ' ' + new URL(request.url).pathname + new URL(request.url).search
            + (request.init.body ? '\n' + JSON.stringify(redact(JSON.parse(request.init.body)), null, 2) : '');
          if (!await confirm(operation, target)) return null;
        }
        const headers = new Headers(request.init.headers);
        // Actual confirmation headers cannot be edited out of the guided client.
        for (const [key, value] of Object.entries(operation['x-cockpit-confirm-headers'] || {})) headers.set(key, value);
        if (auth.mode === 'pat') {
          headers.set('Authorization', 'Bearer ' + auth.token);
        } else {
          headers.delete('Authorization');
        }
        return await fetchOnce(request.url, {...request.init, headers, redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(60000)});
      } finally { busy = false; }
    };
  }
  const shellQuote = value => "'" + String(value).replace(/'/g, "'\\''") + "'";
  function example(request, language) {
    const headers = {...request.init.headers};
    const body = request.init.body;
    if (language === 'python') {
      return 'import os\nimport requests\n\nheaders = ' + JSON.stringify(headers, null, 2)
        + '\nheaders["Authorization"] = "Bearer " + os.environ["ADMIN_PAT"]\n'
        + 'response = requests.request(' + JSON.stringify(request.init.method) + ', ' + JSON.stringify(request.url)
        + ', headers=headers' + (body ? ', data=' + JSON.stringify(body) : '') + ', timeout=60, allow_redirects=False)\n'
        + 'response.raise_for_status()\n' + (new URL(request.url).pathname.endsWith('/export') ? 'with open("usage.xlsx", "wb") as output:\n    output.write(response.content)' : 'print(response.json())')
        + '\n# 写请求结果不明确时先核对，不自动重试；HTTP 200 也要检查逐项结果。';
    }
    if (language === 'javascript') {
      return 'const headers = ' + JSON.stringify(headers, null, 2) + ';\nheaders.Authorization = `Bearer ${process.env.ADMIN_PAT}`;\n'
        + 'const response = await fetch(' + JSON.stringify(request.url) + ', {\n  method: ' + JSON.stringify(request.init.method) + ',\n  headers,\n  redirect: "error",\n  signal: AbortSignal.timeout(60000),'
        + (body ? '\n  body: ' + JSON.stringify(body) + ',' : '') + '\n});\n'
        + 'if (!response.ok) throw new Error(`HTTP ${response.status}: ${await response.text()}`);\n'
        + (new URL(request.url).pathname.endsWith('/export') ? 'const bytes = await response.arrayBuffer(); // 保存为 usage.xlsx' : 'console.log(await response.json());') + '\n// 不自动重试；批量响应需要核对逐项结果。';
    }
    return ['curl --fail-with-body --max-time 60 -X ' + request.init.method + ' ' + shellQuote(request.url),
      '  -H "Authorization: Bearer $ADMIN_PAT"', ...Object.entries(headers).map(([key, value]) => '  -H ' + shellQuote(key + ': ' + value)),
      ...(body ? ['  --data-raw ' + shellQuote(body)] : []), ...(new URL(request.url).pathname.endsWith('/export') ? ['  -o usage.xlsx'] : [])].join(' \\\n');
  }
  const api = {operations, policy, buildRequest, createTransport, redact, example};
  if (typeof module === 'object' && module.exports) module.exports = api;
  else window.CockpitDocsClient = api;
})();
