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
  let modelController = null;
  let modelGeneration = 0;
  let modelTimer = null;

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
    byId('ai-key').disabled = !connection;
    byId('ai-endpoint').disabled = connection?.custom_endpoint !== true;
    byId('ai-key').placeholder = draftKey ? 'Configured; leave blank to keep it'
      : !connection ? 'Choose a provider first'
      : connection.configured ? 'Use existing credential or login'
      : connection.kind === 'api' ? 'API key' : 'Optional; use existing login';
  }

  function cancelModels() {
    modelGeneration += 1;
    clearTimeout(modelTimer);
    if (modelController) modelController.abort();
    modelController = null;
    byId('ai-model-refresh').disabled = !connections.has(byId('ai-provider').value);
  }

  function resetModels(preferred = '') {
    cancelModels();
    const connection = connections.get(byId('ai-provider').value);
    byId('ai-model').replaceChildren();
    const option = document.createElement('option');
    option.value = '';
    option.textContent = !connection ? 'Choose a provider first' : connection.kind === 'agent'
      ? 'Default (use agent settings)' : 'Loading available models…';
    byId('ai-model').append(option);
    if (preferred) {
      const saved = document.createElement('option');
      saved.value = preferred;
      saved.textContent = preferred;
      byId('ai-model').append(saved);
      byId('ai-model').value = preferred;
    }
    byId('ai-model').disabled = !connection || (connection.requires_model && !preferred);
    byId('ai-model-status').textContent = '';
  }

  function scheduleModels() {
    resetModels();
    modelTimer = setTimeout(loadModels, 700);
  }

  async function loadModels() {
    cancelModels();
    const revision = modelGeneration;
    const connection = connections.get(byId('ai-provider').value);
    if (!connection) return;
    const configuration = {
      provider: connection.id,
      endpoint: connection.custom_endpoint ? byId('ai-endpoint').value.trim() : connection.endpoint || '',
      api_key: byId('ai-key').value.trim() || draftKey,
    };
    if (connection.kind === 'api' && ((!configuration.api_key && !connection.configured && !connection.custom_endpoint)
      || !configuration.endpoint)) {
      byId('ai-model').options[0].textContent = 'Connect to load models';
      byId('ai-model-status').textContent = '';
      return;
    }
    const preferred = byId('ai-model').value;
    const active = new AbortController();
    modelController = active;
    byId('ai-model-refresh').disabled = true;
    byId('ai-model-status').textContent = 'Loading available models…';
    const timeout = setTimeout(() => active.abort(), 30000);
    try {
      let result;
      if (token) {
        const response = await fetch('/api/models', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-LineScope-Token': token },
          credentials: 'omit', cache: 'no-store', body: JSON.stringify(configuration), signal: active.signal,
        });
        result = await response.json();
        if (!response.ok) throw new Error(result.error || 'Could not load available models.');
      } else {
        result = await window.LineScopeAI.listModels(configuration, active.signal);
      }
      if (revision !== modelGeneration || !settingsDialog.open) return;
      if (!result || !Array.isArray(result.models)) throw new Error('The provider returned an invalid model catalog.');
      byId('ai-model').replaceChildren();
      if (connection.kind === 'agent') {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = 'Default (use agent settings)';
        byId('ai-model').append(option);
      }
      for (const model of result.models) {
        if (!model || typeof model.id !== 'string' || typeof model.label !== 'string') continue;
        const option = document.createElement('option');
        option.value = model.id;
        option.textContent = model.label;
        byId('ai-model').append(option);
      }
      const options = Array.from(byId('ai-model').options);
      const candidate = options.some(option => option.value === preferred) ? preferred
        : connection.kind === 'agent' ? '' : result.default_model;
      byId('ai-model').value = options.some(option => option.value === candidate) ? candidate : options[0]?.value || '';
      byId('ai-model').disabled = !options.length;
      byId('ai-model-status').textContent = '';
    } catch (error) {
      if (revision !== modelGeneration || !settingsDialog.open) return;
      byId('ai-model-status').textContent = error.name === 'AbortError'
        ? 'Loading models timed out. Refresh to retry.' : error.message || 'Could not load available models.';
      if (connection.kind === 'api' && !byId('ai-model').value) byId('ai-model').options[0].textContent = 'Refresh to load models';
    } finally {
      clearTimeout(timeout);
      if (revision === modelGeneration) {
        modelController = null;
        byId('ai-model-refresh').disabled = false;
      }
    }
  }

  function connectionProblem(configuration = settings) {
    const connection = connections.get(configuration.provider);
    if (!connection) return 'Choose a model provider.';
    if (!connection.available) return 'This provider is unavailable.';
    if (connection.requires_model && !configuration.model) return 'Load and select an available model.';
    if (connection.kind === 'api' && !configuration.endpoint) return 'Enter the custom API endpoint.';
    if (connection.kind === 'api' && !connection.custom_endpoint && !connection.configured && !configuration.api_key) return 'Enter a credential.';
    return '';
  }

  function openSettings(message = '') {
    draftKey = settings.api_key;
    draftEndpoint = settings.endpoint;
    byId('ai-provider').value = settings.provider;
    byId('ai-endpoint').value = settings.endpoint;
    byId('ai-key').value = '';
    byId('ai-settings-status').textContent = message;
    updateProvider();
    resetModels(settings.model);
    settingsDialog.showModal();
    loadModels();
  }

  function openSuggestion() {
    if (!target) return;
    cancelRequest();
    byId('ai-target').textContent = target.label;
    byId('ai-suggestion-title').textContent = target.scope === 'spark' ? 'Improve Spark performance' : 'Improve this line';
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
            scope: payload.scope, execution_id: payload.execution_id,
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
      scope: target.scope,
      execution_id: target.execution,
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
    resetModels();
    loadModels();
  });
  byId('ai-endpoint').addEventListener('input', () => {
    if (byId('ai-endpoint').value.trim() !== draftEndpoint) {
      const hadCredential = Boolean(draftKey || byId('ai-key').value);
      draftKey = '';
      byId('ai-key').value = '';
      draftEndpoint = byId('ai-endpoint').value.trim();
      updateProvider();
      if (hadCredential) byId('ai-settings-status').textContent = endpointCredentialMessage;
      scheduleModels();
    }
  });
  byId('ai-key').addEventListener('input', () => {
    if (byId('ai-key').value.trim() && byId('ai-settings-status').textContent === endpointCredentialMessage) {
      byId('ai-settings-status').textContent = '';
    }
    scheduleModels();
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
    cancelModels();
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
    const button = event.target.closest('.ai-line-button, .ai-spark-button');
    if (!button) return;
    event.preventDefault();
    cancelRequest();
    activation = { button, pointer: event.detail > 0 };
    button.classList.remove('ai-pointer-dismissed');
    target = {
      source: button.dataset.aiSource,
      line: button.dataset.aiLine ? Number(button.dataset.aiLine) : undefined,
      scope: button.dataset.aiScope || 'line',
      execution: button.dataset.aiExecution,
      label: button.dataset.aiLabel,
    };
    const problem = connectionProblem();
    if (problem) {
      pendingLine = true;
      openSettings(problem);
      return;
    }
    openSuggestion();
  });
  byId('ai-goal').addEventListener('change', invalidatePrompt);
  byId('ai-model-refresh').addEventListener('click', loadModels);
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
