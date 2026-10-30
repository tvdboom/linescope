(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const viewerOrigin = document.querySelector('meta[name="linescope-ai-origin"]')?.content;
  const token = viewerOrigin && viewerOrigin === window.location.origin
    ? document.querySelector('meta[name="linescope-ai-token"]')?.content : null;
  const settingsDialog = byId('ai-settings');
  const suggestionDialog = byId('ai-suggestion');
  const quickDialog = byId('ai-quick');
  const quickStatus = byId('ai-quick-status');
  const settingsToggle = byId('ai-settings-toggle');
  const status = byId('ai-suggestion-status');
  const prompt = byId('ai-prompt');
  const preview = byId('ai-preview-details');
  const previewSummary = preview.querySelector('summary');
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
  let quickGeneration = 0;
  let promptCurrent = false;
  let promptError = '';
  let promptTimer = null;
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

  function setModelsLoading(loading) {
    const connection = connections.get(byId('ai-provider').value);
    const model = byId('ai-model');
    const refresh = byId('ai-model-refresh');
    model.disabled = loading || !connection || (connection.requires_model && !model.value);
    refresh.disabled = loading || !connection;
    refresh.setAttribute('aria-busy', String(loading));
    refresh.setAttribute('aria-label', loading ? 'Loading available models' : 'Refresh models');
    refresh.title = loading ? 'Loading available models' : 'Refresh models';
    model.parentElement.setAttribute('aria-busy', String(loading));
  }

  function cancelModels() {
    modelGeneration += 1;
    clearTimeout(modelTimer);
    modelTimer = null;
    if (modelController) modelController.abort();
    modelController = null;
    setModelsLoading(false);
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
    setModelsLoading(true);
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
    setModelsLoading(true);
    byId('ai-model-status').textContent = '';
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
        setModelsLoading(false);
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
    byId('ai-answer').textContent = '';
    preview.open = false;
    suggestionDialog.showModal();
    refreshPrompt();
  }

  function updateControls() {
    const busy = Boolean(controller);
    const preparing = promptTimer !== null || (requestKind === 'prompt' && !target?.quick && busy);
    const previewAvailable = promptCurrent && !preparing;
    preview.setAttribute('aria-busy', String(preparing));
    previewSummary.setAttribute('aria-disabled', String(!previewAvailable));
    previewSummary.tabIndex = previewAvailable ? 0 : -1;
    previewSummary.title = promptError ? promptError + ' Select Generate suggestion to retry.' : '';
    byId('ai-preview-spinner').hidden = !preparing;
    byId('ai-preview-label').textContent = preparing ? 'Updating context…'
      : promptError ? 'Context unavailable' : 'Context sent';
    previewSummary.setAttribute('aria-label', byId('ai-preview-label').textContent);
    if (!previewAvailable) preview.open = false;
    byId('ai-generate').disabled = busy || promptTimer !== null || (promptCurrent && !prompt.value.trim());
    byId('ai-cancel').hidden = requestKind !== 'suggestion';
    prompt.readOnly = !promptCurrent;
    suggestionDialog.setAttribute('aria-busy', String(busy || preparing));
    quickDialog.setAttribute('aria-busy', String(busy));
  }

  function cancelRequest(message = '') {
    generation += 1;
    clearTimeout(promptTimer);
    promptTimer = null;
    if (controller) controller.abort();
    controller = null;
    requestKind = '';
    status.textContent = message;
    quickStatus.textContent = '';
    updateControls();
  }

  async function request(path, payload, kind) {
    cancelRequest();
    const revision = generation;
    const active = new AbortController();
    controller = active;
    requestKind = kind;
    updateControls();
    const requestStatus = target?.quick ? quickStatus : status;
    requestStatus.textContent = target?.quick ? 'Generating…'
      : kind === 'prompt' ? '' : 'Requesting a suggestion…';
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
            end_line: payload.end_line, selected_lines: payload.selected_lines, response_mode: payload.response_mode,
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
        const message = error.name === 'AbortError'
          ? 'The request timed out. Try again or choose another provider.'
          : (error.message || 'The AI connection could not complete the request.');
        if (kind === 'prompt' && !target?.quick) promptError = message;
        else requestStatus.textContent = message;
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

  function promptOptions() {
    return {
      source_id: target.source,
      line: target.line,
      end_line: target.endLine,
      selected_lines: target.selectedLines,
      scope: target.scope,
      execution_id: target.execution,
      goal: byId('ai-goal').querySelector('input:checked').value,
      problem: byId('ai-problem').value,
      include_related: byId('ai-related').checked,
    };
  }

  async function refreshPrompt() {
    if (!target) return;
    promptCurrent = false;
    promptError = '';
    prompt.value = '';
    byId('ai-answer-section').hidden = true;
    const response = await request('/api/prompt', promptOptions(), 'prompt');
    if (!response) return;
    if (typeof response.prompt !== 'string' || !response.prompt.trim()) {
      promptError = 'No usable context was returned by the viewer.';
      updateControls();
      return;
    }
    prompt.value = response.prompt;
    promptCurrent = true;
    status.textContent = '';
    updateControls();
  }

  function schedulePrompt() {
    cancelRequest();
    promptCurrent = false;
    promptError = '';
    prompt.value = '';
    byId('ai-answer-section').hidden = true;
    // Coalesce typing and discard earlier requests before rebuilding context.
    promptTimer = setTimeout(() => {
      promptTimer = null;
      refreshPrompt();
    }, 250);
    updateControls();
  }

  async function openQuick() {
    if (!target) return;
    const revision = ++quickGeneration;
    cancelRequest();
    const answer = byId('ai-quick-answer');
    const explanation = byId('ai-quick-explanation');
    answer.textContent = '';
    answer.hidden = true;
    explanation.textContent = '';
    explanation.hidden = true;
    byId('ai-quick-retry').hidden = true;
    if (!quickDialog.open) quickDialog.showModal();
    const context = await request('/api/prompt', {
      ...promptOptions(), response_mode: 'quick', goal: 'time',
    }, 'prompt');
    if (revision !== quickGeneration || !quickDialog.open) return;
    if (!context || typeof context.prompt !== 'string' || !context.prompt.trim()) {
      if (quickDialog.open) {
        if (!quickStatus.textContent) quickStatus.textContent = 'No usable context was returned.';
        byId('ai-quick-retry').hidden = false;
      }
      return;
    }
    const response = await request('/api/suggest', {
      ...settings, prompt: context.prompt,
    }, 'suggestion');
    if (revision !== quickGeneration || !quickDialog.open) return;
    if (!response || typeof response.suggestion !== 'string' || !response.suggestion.trim()) {
      if (quickDialog.open) {
        if (!quickStatus.textContent) quickStatus.textContent = 'The provider returned no suggestion.';
        byId('ai-quick-retry').hidden = false;
      }
      return;
    }
    const captured = JSON.parse(byId('linescope-ai-context').textContent);
    const sourceId = captured.source_anchors?.[target.source] || target.source;
    const result = window.LineScopeAI.formatQuickSuggestion(
      response.suggestion, captured.sources[sourceId]?.source || '', target.line, target.endLine,
    );
    // Provider output stays inert; explanatory prose is separate from code.
    answer.textContent = result.code;
    answer.hidden = !result.code;
    explanation.textContent = result.explanation;
    explanation.hidden = !result.explanation;
    quickStatus.textContent = '';
    (result.code ? answer : explanation).focus();
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
    if (continueLine) {
      if (target?.quick) openQuick();
      else openSuggestion();
    }
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
  quickDialog.addEventListener('close', () => {
    quickGeneration += 1;
    cancelRequest();
    restoreLineFocus();
  });
  byId('ai-quick-retry').addEventListener('click', openQuick);

  function restoreLineFocus() {
    if (!activation || settingsDialog.open || suggestionDialog.open || quickDialog.open) return;
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

  document.querySelectorAll('.ai-source-action').forEach(button => {
    const clearDismissal = () => button.classList.remove('ai-pointer-dismissed');
    const row = button.closest('tr');
    row?.addEventListener('pointerleave', clearDismissal);
    row?.addEventListener('pointerenter', clearDismissal);
    button.addEventListener('focus', () => {
      if (button.matches(':focus-visible')) clearDismissal();
    });
  });
  document.addEventListener('click', event => {
    const button = event.target.closest('.ai-source-action, .ai-spark-button, [data-ai-selection]');
    if (!button) return;
    event.preventDefault();
    cancelRequest();
    activation = { button, pointer: event.detail > 0 };
    button.classList.remove('ai-pointer-dismissed');
    target = {
      source: button.dataset.aiSource,
      line: button.dataset.aiLine ? Number(button.dataset.aiLine) : undefined,
      endLine: button.dataset.aiEndLine ? Number(button.dataset.aiEndLine) : undefined,
      selectedLines: button.dataset.aiLines ? JSON.parse(button.dataset.aiLines) : undefined,
      quick: button.dataset.aiQuick === 'true',
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
    if (target.quick) openQuick();
    else openSuggestion();
  });
  byId('ai-goal').addEventListener('change', schedulePrompt);
  previewSummary.addEventListener('click', event => {
    if (previewSummary.getAttribute('aria-disabled') === 'true') event.preventDefault();
  });
  byId('ai-model-refresh').addEventListener('click', loadModels);
  byId('ai-problem').addEventListener('input', schedulePrompt);
  byId('ai-related').addEventListener('change', schedulePrompt);
  prompt.addEventListener('input', updateControls);
  byId('ai-cancel').addEventListener('click', () => {
    cancelRequest('Stopped waiting. The provider may still finish the request.');
  });
  byId('ai-generate').addEventListener('click', async () => {
    if (controller || promptTimer !== null) return;
    // A failed context load can be retried through the primary action.
    if (!promptCurrent) await refreshPrompt();
    if (!promptCurrent || !prompt.value.trim() || controller || !suggestionDialog.open) return;
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
