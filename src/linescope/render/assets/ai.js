(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const viewerOrigin = document.querySelector('meta[name="linescope-ai-origin"]')?.content;
  const token = viewerOrigin && viewerOrigin === window.location.origin
    ? document.querySelector('meta[name="linescope-ai-token"]')?.content : null;
  const settingsDialog = byId('ai-settings');
  const suggestionDialog = byId('ai-suggestion');
  const settingsToggle = byId('ai-settings-toggle');
  const status = byId('ai-suggestion-status');
  const prompt = byId('ai-prompt');
  const endpointCredentialMessage = 'The endpoint changed, so its credential was cleared. Enter the credential for this address again.';
  const fallbackConnections = Array.from(byId('ai-provider').options)
    .filter(option => option.value)
    .map(option => ({ id: option.value, label: option.textContent, kind: '', configured: false, available: false, requires_model: false, endpoint: '' }));
  let connectionMetadata = { connections: fallbackConnections, default_connection: null };
  try {
    const metadata = JSON.parse(
      (token && document.querySelector('meta[name="linescope-ai-connections"]')?.content)
      || document.querySelector('meta[name="linescope-ai-catalog"]')?.content
      || 'null'
    );
    if (metadata && Array.isArray(metadata.connections)) connectionMetadata = metadata;
  } catch {
    // Damaged connection metadata stays inert instead of choosing a provider.
  }
  const connections = new Map(connectionMetadata.connections.filter(connection =>
    connection && typeof connection.id === 'string' && typeof connection.label === 'string'
    && (connection.kind === 'api' || connection.available === true)
  ).map(connection => [connection.id, connection]));
  const initialConnection = connections.get(connectionMetadata.default_connection);
  let settings = {
    provider: initialConnection?.id || '',
    model: '',
    endpoint: initialConnection?.endpoint || '',
    api_key: '',
  };
  let draftKey = '';
  let draftEndpoint = '';
  let pendingLine = false;
  let activation = null;
  let target = null;
  let controller = null;
  let generation = 0;
  let promptCurrent = false;
  let requestKind = '';

  // Saved reports use preset APIs directly; the viewer also runs local agents.
  byId('ai-provider').replaceChildren();
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.textContent = 'Choose a provider';
  byId('ai-provider').append(placeholder);
  connections.forEach(connection => {
    const option = document.createElement('option');
    option.value = connection.id;
    option.textContent = connection.label;
    byId('ai-provider').append(option);
  });
  byId('ai-provider').value = settings.provider;

  function updateConfigurationState() {
    const ready = !connectionProblem();
    const label = ready ? 'AI suggestions configured: ' + connections.get(settings.provider).label
      : 'Configure AI suggestions';
    settingsToggle.setAttribute('data-ai-ready', String(ready));
    settingsToggle.setAttribute('aria-label', label);
    settingsToggle.setAttribute('title', label);
    settingsToggle.setAttribute('aria-haspopup', 'dialog');
  }
  updateConfigurationState();

  function updateProvider() {
    const connection = connections.get(byId('ai-provider').value);
    byId('ai-api-fields').hidden = connection?.custom_endpoint !== true;
    byId('ai-model').disabled = !connection;
    byId('ai-key').disabled = !connection;
    byId('ai-endpoint').disabled = connection?.custom_endpoint !== true;
    byId('ai-model').placeholder = !connection ? 'Choose a provider first'
      : connection.requires_model ? 'Model ID' : 'Optional; use the provider default';
    byId('ai-key').placeholder = draftKey ? 'Configured; leave blank to keep it'
      : !connection ? 'Choose a provider first'
      : connection.configured ? 'Use existing credential or login'
      : connection.kind === 'api' ? 'API key' : 'Optional; use existing login';
  }

  function connectionProblem(configuration = settings) {
    const connection = connections.get(configuration.provider);
    if (!connection) return 'Choose a model provider.';
    if (!connection.available) return 'This provider is unavailable.';
    if (connection.requires_model && !configuration.model) return 'Enter a model.';
    if (connection.kind === 'api' && !configuration.endpoint) return 'Enter the custom API endpoint.';
    if (connection.kind === 'api' && !connection.custom_endpoint && !connection.configured && !configuration.api_key) return 'Enter a credential.';
    return '';
  }

  function openSettings(message = '') {
    draftKey = settings.api_key;
    draftEndpoint = settings.endpoint;
    byId('ai-provider').value = settings.provider;
    byId('ai-model').value = settings.model;
    byId('ai-endpoint').value = settings.endpoint;
    byId('ai-key').value = '';
    byId('ai-settings-status').textContent = message;
    updateProvider();
    settingsDialog.showModal();
  }

  function openSuggestion() {
    if (!target) return;
    cancelRequest();
    byId('ai-target').textContent = target.label;
    byId('ai-answer').textContent = '';
    byId('ai-preview-details').open = true;
    suggestionDialog.showModal();
    refreshPrompt();
  }

  function updateControls() {
    const busy = Boolean(controller);
    byId('ai-generate').disabled = busy || !promptCurrent || !prompt.value.trim();
    byId('ai-refresh').disabled = busy;
    byId('ai-cancel').hidden = !busy;
    suggestionDialog.setAttribute('aria-busy', String(busy));
  }

  function cancelRequest(message = '') {
    generation += 1;
    if (controller) controller.abort();
    controller = null;
    requestKind = '';
    status.textContent = message;
    updateControls();
  }

  async function request(path, payload, kind) {
    cancelRequest();
    const revision = generation;
    const active = new AbortController();
    controller = active;
    requestKind = kind;
    updateControls();
    status.textContent = kind === 'prompt' ? 'Preparing captured context…' : 'Requesting a suggestion…';
    const timeout = setTimeout(() => active.abort(), kind === 'prompt' ? 30000 : 180000);
    try {
      let data;
      if (!token) {
        if (kind === 'prompt') {
          // The inert snapshot script follows this script in the saved file.
          const context = JSON.parse(byId('linescope-ai-context').textContent);
          data = { prompt: window.LineScopeAI.buildPrompt(context, payload.source_id, payload.line, {
            goal: payload.goal, problem: payload.problem, include_related: payload.include_related,
          }) };
        } else {
          data = { suggestion: await window.LineScopeAI.requestSuggestion(payload, payload.prompt, active.signal) };
        }
      } else {
        const response = await fetch(path, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-LineScope-Token': token },
          credentials: 'omit',
          cache: 'no-store',
          body: JSON.stringify(payload),
          signal: active.signal,
        });
        data = await response.json();
        if (!response.ok) throw new Error(data.error || 'The viewer could not complete the request.');
      }
      if (revision !== generation) return null;
      if (active.signal.aborted) {
        const error = new Error('Request timed out.');
        error.name = 'AbortError';
        throw error;
      }
      return data;
    } catch (error) {
      if (revision === generation) {
        status.textContent = error.name === 'AbortError'
          ? 'The request timed out. Try again or choose another provider.'
          : (error.message || 'The AI connection could not complete the request.');
      }
      return null;
    } finally {
      clearTimeout(timeout);
      if (revision === generation) {
        controller = null;
        requestKind = '';
        updateControls();
      }
    }
  }

  async function refreshPrompt() {
    if (!target) return;
    promptCurrent = false;
    prompt.value = '';
    byId('ai-answer-section').hidden = true;
    const response = await request('/api/prompt', {
      source_id: target.source,
      line: target.line,
      goal: byId('ai-goal').value,
      problem: byId('ai-problem').value,
      include_related: byId('ai-related').checked,
    }, 'prompt');
    if (!response) return;
    if (typeof response.prompt !== 'string' || !response.prompt.trim()) {
      status.textContent = 'No usable context was returned by the viewer.';
      return;
    }
    prompt.value = response.prompt;
    promptCurrent = true;
    status.textContent = 'Context ready. Generate sends the preview to your selected AI connection.';
    updateControls();
  }

  function invalidatePrompt() {
    if (controller) cancelRequest('Context changed. Update context before generating.');
    else status.textContent = 'Context changed. Update context before generating.';
    promptCurrent = false;
    updateControls();
  }

  settingsToggle.addEventListener('click', () => {
    activation = null;
    pendingLine = false;
    openSettings();
  });
  byId('ai-provider').addEventListener('change', () => {
    // A credential belongs to the selected connection, never the next one.
    draftKey = '';
    byId('ai-key').value = '';
    byId('ai-model').value = '';
    const connection = connections.get(byId('ai-provider').value);
    byId('ai-endpoint').value = connection?.endpoint || '';
    draftEndpoint = byId('ai-endpoint').value;
    byId('ai-settings-status').textContent = '';
    updateProvider();
  });
  byId('ai-endpoint').addEventListener('input', () => {
    if (byId('ai-endpoint').value.trim() !== draftEndpoint) {
      const hadCredential = Boolean(draftKey || byId('ai-key').value);
      draftKey = '';
      byId('ai-key').value = '';
      draftEndpoint = byId('ai-endpoint').value.trim();
      updateProvider();
      if (hadCredential) byId('ai-settings-status').textContent = endpointCredentialMessage;
    }
  });
  byId('ai-key').addEventListener('input', () => {
    if (byId('ai-key').value.trim() && byId('ai-settings-status').textContent === endpointCredentialMessage) {
      byId('ai-settings-status').textContent = '';
    }
  });
  byId('ai-settings-form').addEventListener('submit', event => {
    event.preventDefault();
    const provider = byId('ai-provider').value;
    const connection = connections.get(provider);
    const endpoint = connection?.kind === 'api'
      ? (connection.custom_endpoint ? byId('ai-endpoint').value.trim() : connection.endpoint || '') : '';
    const enteredKey = byId('ai-key').value.trim();
    const key = enteredKey || draftKey;
    const candidate = { provider, endpoint, model: byId('ai-model').value.trim(), api_key: key };
    byId('ai-key').value = '';
    draftKey = key;
    const problem = connectionProblem(candidate);
    if (problem) {
      byId('ai-settings-status').textContent = problem;
      updateProvider();
      return;
    }
    settings = candidate;
    updateConfigurationState();
    const continueLine = pendingLine;
    pendingLine = false;
    settingsDialog.close();
    if (continueLine) openSuggestion();
  });
  document.querySelectorAll('[data-ai-close]').forEach(button => {
    button.addEventListener('click', () => byId(button.dataset.aiClose).close());
  });
  settingsDialog.addEventListener('close', () => {
    byId('ai-key').value = '';
    draftKey = '';
    pendingLine = false;
    restoreLineFocus();
  });
  suggestionDialog.addEventListener('close', () => {
    cancelRequest();
    restoreLineFocus();
  });

  function restoreLineFocus() {
    if (!activation || settingsDialog.open || suggestionDialog.open) return;
    const { button, pointer } = activation;
    activation = null;
    if (pointer) {
      button.blur();
      button.classList.add('ai-pointer-dismissed');
    } else {
      button.classList.remove('ai-pointer-dismissed');
      button.focus({ preventScroll: true });
    }
  }

  document.querySelectorAll('.ai-line-button').forEach(button => {
    const clearDismissal = () => button.classList.remove('ai-pointer-dismissed');
    const row = button.closest('tr');
    row?.addEventListener('pointerleave', clearDismissal);
    row?.addEventListener('pointerenter', clearDismissal);
    button.addEventListener('focus', () => {
      if (button.matches(':focus-visible')) clearDismissal();
    });
  });
  document.addEventListener('click', event => {
    const button = event.target.closest('.ai-line-button');
    if (!button) return;
    event.preventDefault();
    cancelRequest();
    activation = { button, pointer: event.detail > 0 };
    button.classList.remove('ai-pointer-dismissed');
    target = { source: button.dataset.aiSource, line: Number(button.dataset.aiLine), label: button.dataset.aiLabel };
    const problem = connectionProblem();
    if (problem) {
      pendingLine = true;
      openSettings(problem);
      return;
    }
    openSuggestion();
  });
  byId('ai-goal').addEventListener('change', invalidatePrompt);
  byId('ai-problem').addEventListener('input', invalidatePrompt);
  byId('ai-related').addEventListener('change', invalidatePrompt);
  prompt.addEventListener('input', updateControls);
  byId('ai-refresh').addEventListener('click', refreshPrompt);
  byId('ai-cancel').addEventListener('click', () => {
    const wasSuggestion = requestKind === 'suggestion';
    cancelRequest(wasSuggestion ? 'Stopped waiting. The provider may still finish the request.' : 'Context request canceled.');
  });
  byId('ai-generate').addEventListener('click', async () => {
    if (!promptCurrent || !prompt.value.trim() || controller) return;
    byId('ai-answer-section').hidden = true;
    const response = await request('/api/suggest', {
      provider: settings.provider,
      model: settings.model,
      endpoint: settings.endpoint,
      api_key: settings.api_key,
      prompt: prompt.value,
    }, 'suggestion');
    if (!response) return;
    if (typeof response.suggestion !== 'string' || !response.suggestion.trim()) {
      status.textContent = 'The provider returned no suggestion. Try again or choose another model.';
      return;
    }
    // Model output is untrusted text, including any proposed code or markup.
    byId('ai-answer').textContent = response.suggestion;
    byId('ai-answer-section').hidden = false;
    status.textContent = 'Suggestion ready.';
    byId('ai-answer').focus();
  });
})();
