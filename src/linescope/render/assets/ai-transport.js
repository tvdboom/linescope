(() => {
  'use strict';
  const MAX_RESPONSE = 1000000;
  const presetError = 'This connection uses its preset endpoint. Choose Custom API for another URL.';
  const networkError = 'The browser could not reach the provider. Check the endpoint and its CORS permissions, or open this report with the local viewer.';
  const controlledErrors = new WeakSet();

  function failed(message) {
    const error = new Error(message);
    controlledErrors.add(error);
    return error;
  }

  function textResult(payload, protocol) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload) || payload.error) {
      throw failed('The provider returned an unsuccessful response.');
    }
    const texts = [];
    if (protocol === 'responses') {
      if (payload.status != null && payload.status !== 'completed') {
        throw failed('The provider did not complete the suggestion. Try again.');
      }
      if (Array.isArray(payload.output)) {
        for (const item of payload.output) {
          if (!item || item.type !== 'message' || !Array.isArray(item.content)) continue;
          for (const part of item.content) {
            if (part && part.type === 'output_text' && typeof part.text === 'string') texts.push(part.text);
          }
        }
      }
    } else if (protocol === 'anthropic-messages') {
      if (!['end_turn', 'stop_sequence'].includes(payload.stop_reason)) {
        throw failed('The provider did not complete the suggestion. Try again.');
      }
      if (Array.isArray(payload.content)) {
        for (const part of payload.content) {
          if (part && part.type === 'text' && typeof part.text === 'string') texts.push(part.text);
        }
      }
    } else {
      const choice = Array.isArray(payload.choices) ? payload.choices[0] : null;
      if (choice && ['length', 'content_filter', 'tool_calls'].includes(choice.finish_reason)) {
        throw failed('The provider did not complete the suggestion. Try again.');
      }
      if (choice && typeof choice.message?.content === 'string') texts.push(choice.message.content);
    }
    const text = texts.join('\n').trim();
    if (!text) throw failed('The provider returned no suggestion text.');
    return text;
  }

  const adapters = {
    responses: {
      requiresKey: true,
      body: (model, prompt) => ({ model, input: prompt, store: false }),
    },
    'chat-completions': {
      requiresKey: false,
      body: (model, prompt) => ({ model, messages: [{ role: 'user', content: prompt }], stream: false }),
    },
    'anthropic-messages': {
      requiresKey: true,
      headers: {
        'anthropic-version': '2023-06-01',
        'anthropic-dangerous-direct-browser-access': 'true',
      },
      body: (model, prompt) => ({ model, max_tokens: 4096, messages: [{ role: 'user', content: prompt }], stream: false }),
    },
  };

  function connectionFor(id) {
    let catalog;
    try {
      catalog = JSON.parse(document.querySelector('meta[name="linescope-ai-catalog"]')?.content || '{}');
    } catch {
      throw failed('This report has invalid AI connection metadata. Regenerate the report.');
    }
    const connection = Array.isArray(catalog.connections)
      ? catalog.connections.find(entry => entry && entry.id === id) : null;
    if (!connection || connection.kind !== 'api' || !Object.hasOwn(adapters, connection.protocol)) {
      throw failed('Choose an API connection, or use the local viewer for a native agent.');
    }
    return connection;
  }

  function endpointFor(connection, supplied) {
    if (typeof supplied !== 'string') throw failed('Enter a valid provider endpoint URL.');
    const custom = supplied.trim();
    if (!connection.custom_endpoint && custom && custom !== connection.endpoint) throw failed(presetError);
    const endpoint = connection.custom_endpoint ? custom : connection.endpoint;
    let url;
    try {
      url = new URL(endpoint);
    } catch {
      throw failed('Enter a valid provider endpoint URL.');
    }
    if (url.username || url.password || url.search || url.hash || /\s/.test(endpoint) || url.port === '0') {
      throw failed('Use an endpoint URL without credentials, query, or fragment.');
    }
    const loopback = url.hostname === 'localhost' || url.hostname === '[::1]' || /^127\.\d+\.\d+\.\d+$/.test(url.hostname);
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && loopback)) {
      throw failed('Provider endpoints require HTTPS, except on localhost.');
    }
    return url.href;
  }

  async function boundedJSON(response) {
    if (Number(response.headers.get('Content-Length')) > MAX_RESPONSE) {
      throw failed('The provider response exceeded the 1 MB limit.');
    }
    const reader = response.body?.getReader();
    if (!reader) throw failed('The provider returned invalid JSON.');
    const decoder = new TextDecoder('utf-8', { fatal: true });
    const chunks = [];
    let size = 0;
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > MAX_RESPONSE) throw failed('The provider response exceeded the 1 MB limit.');
        try {
          chunks.push(decoder.decode(value, { stream: true }));
        } catch {
          throw failed('The provider returned invalid JSON.');
        }
      }
      try {
        chunks.push(decoder.decode());
        return JSON.parse(chunks.join(''));
      } catch {
        throw failed('The provider returned invalid JSON.');
      }
    } finally {
      try { await reader.cancel(); } catch { /* Fetch cancellation already owns cleanup. */ }
      reader.releaseLock();
    }
  }

  async function requestProvider(settings, prompt, signal, catalog = false, cursor = '') {
    if (!settings || typeof settings !== 'object') throw failed('Choose an API connection.');
    const connection = connectionFor(settings.provider);
    const adapter = adapters[connection.protocol];
    if (!catalog && (typeof settings.model !== 'string' || !settings.model.trim() || settings.model.length > 256)) {
      throw failed('Enter a model identifier of at most 256 characters.');
    }
    if (!catalog && (typeof prompt !== 'string' || !prompt.trim() || new TextEncoder().encode(prompt).byteLength > 500000)) {
      throw failed('Provide a nonempty prompt of at most 500 KB.');
    }
    let endpoint = endpointFor(connection, settings.endpoint || '');
    if (catalog) {
      const url = new URL(endpoint);
      const path = url.pathname.replace(/\/$/, '');
      if (connection.custom_endpoint && !path.endsWith('/chat/completions')) {
        throw failed('Use a Chat Completions endpoint to discover its models.');
      }
      url.pathname = connection.custom_endpoint ? path.slice(0, -'/chat/completions'.length) + '/models'
        : path.slice(0, path.lastIndexOf('/')) + '/models';
      if (connection.protocol === 'anthropic-messages') {
        url.searchParams.set('limit', '1000');
        if (cursor) url.searchParams.set('after_id', cursor);
      }
      endpoint = url.href;
    }
    if (typeof settings.api_key !== 'string') throw failed('Enter a valid connection credential.');
    const key = settings.api_key.trim();
    if (key.length > 8192 || /[^\x20-\x7e]/.test(key)) {
      throw failed('The API key must contain at most 8192 printable ASCII characters.');
    }
    if (adapter.requiresKey && !key) throw failed('Enter a credential for this connection.');
    if (signal?.aborted) throw new DOMException('The request was canceled.', 'AbortError');
    const controller = new AbortController();
    const cancel = () => controller.abort();
    signal?.addEventListener('abort', cancel, { once: true });
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, catalog ? 15000 : 60000);
    const headers = { 'Content-Type': 'application/json', Accept: 'application/json', ...adapter.headers };
    if (key) headers.Authorization = 'Bearer ' + key;
    try {
      let response;
      try {
        response = await fetch(endpoint, {
          method: catalog ? 'GET' : 'POST', headers,
          body: catalog ? undefined : JSON.stringify(adapter.body(settings.model.trim(), prompt)),
          mode: 'cors', credentials: 'omit', cache: 'no-store', redirect: 'error',
          referrerPolicy: 'no-referrer', signal: controller.signal,
        });
      } catch {
        if (controller.signal.aborted) throw new DOMException('The request was canceled.', 'AbortError');
        throw failed(networkError);
      }
      if (!response.ok) {
        throw failed('The provider returned HTTP ' + response.status + '. Check the endpoint, key, model, and quota.');
      }
      let payload;
      try {
        payload = await boundedJSON(response);
      } catch (error) {
        if (error.name === 'TypeError') throw failed(networkError);
        throw error;
      }
      if (catalog) return payload;
      const suggestion = textResult(payload, connection.protocol);
      return key ? suggestion.split(key).join('[redacted credential]') : suggestion;
    } catch (error) {
      if (timedOut) throw failed('The provider request timed out. Try again or choose another connection.');
      if (signal?.aborted) throw new DOMException('The request was canceled.', 'AbortError');
      throw controlledErrors.has(error) ? error : failed(networkError);
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', cancel);
      controller.abort();
    }
  }

  async function listModels(settings, signal) {
    const connection = connectionFor(settings.provider);
    const records = [];
    const cursors = new Set();
    let cursor = '';
    for (let page = 0; page < 10; page += 1) {
      const payload = await requestProvider(settings, '', signal, true, cursor);
      if (!payload || !Array.isArray(payload.data)) throw failed('The provider returned an invalid model catalog.');
      records.push(...payload.data);
      if (connection.protocol !== 'anthropic-messages' || !payload.has_more) break;
      if (typeof payload.last_id !== 'string' || !payload.last_id || cursors.has(payload.last_id) || page === 9) {
        throw failed('The provider returned an invalid model cursor.');
      }
      cursor = payload.last_id;
      cursors.add(cursor);
    }
    const key = settings.api_key.trim();
    const choices = new Map();
    for (const record of records) {
      if (!record || typeof record !== 'object' || record.hidden === true) continue;
      const id = record.model ?? record.id;
      if (typeof id !== 'string' || !id || id.length > 200 || id.startsWith('-') || /[\s\x00-\x1f\x7f]/.test(id) || (key && id.includes(key))) continue;
      if (settings.provider === 'openai' && (!/^(gpt-|o1|o3|o4)/.test(id) || /image|audio|realtime|transcribe|search|instruct/.test(id))) continue;
      const family = ['Astra', 'Sol', 'Terra', 'Luna', 'Opus', 'Sonnet', 'Haiku'].find(name => id.split('-').includes(name.toLowerCase()));
      let label = record.displayName || record.display_name || (family ? family + ' · ' + id : id);
      if (typeof label !== 'string') label = id;
      if (key) label = label.split(key).join('[redacted]');
      choices.set(id, { id, label: label.slice(0, 200) });
    }
    const models = Array.from(choices.values()).sort((a, b) => Number(b.id.includes('sol')) - Number(a.id.includes('sol'))
      || (a.id < b.id ? 1 : a.id > b.id ? -1 : 0));
    if (!models.length) throw failed('No text models were returned by this provider.');
    return { models, default_model: models[0].id };
  }

  window.LineScopeAI = window.LineScopeAI || {};
  window.LineScopeAI.requestSuggestion = (settings, prompt, signal) => requestProvider(settings, prompt, signal);
  window.LineScopeAI.listModels = listModels;
})();
