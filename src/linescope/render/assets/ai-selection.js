(() => {
  'use strict';
  const toolbar = document.getElementById('ai-selection');
  const selected = new Set();
  let anchor = null;
  let drag = null;
  let suppressClick = false;

  const action = row => row?.querySelector('.ai-line-button');
  const sameSource = (first, last) => first?.closest('tbody') === last?.closest('tbody')
    && action(first)?.dataset.aiSource === action(last)?.dataset.aiSource;

  function renderSelection() {
    document.querySelectorAll('.source-row.ai-selected').forEach(row => {
      row.classList.remove('ai-selected', 'ai-selection-start', 'ai-selection-end');
      row.querySelector('.line-number a').setAttribute('aria-pressed', 'false');
    });
    toolbar.hidden = selected.size === 0;
    if (toolbar.hidden) return;
    const rows = Array.from(selected);
    for (const row of rows) {
      row.classList.add('ai-selected');
      row.classList.toggle('ai-selection-start', !selected.has(row.previousElementSibling));
      row.classList.toggle('ai-selection-end', !selected.has(row.nextElementSibling));
      row.querySelector('.line-number a').setAttribute('aria-pressed', 'true');
    }
    const lines = rows.map(row => Number(action(row).dataset.aiLine)).sort((a, b) => a - b);
    const ranges = [];
    let start = lines[0];
    let end = start;
    for (const line of lines.slice(1)) {
      if (line === end + 1) end = line;
      else {
        ranges.push(start === end ? String(start) : start + '–' + end);
        start = end = line;
      }
    }
    ranges.push(start === end ? String(start) : start + '–' + end);
    const first = action(rows[0]);
    const label = first.dataset.aiLabel.replace(/:\d+$/, ':' + ranges.join(', '));
    const labelElement = document.getElementById('ai-selection-label');
    labelElement.textContent = label;
    labelElement.title = label;
    toolbar.querySelectorAll('[data-ai-selection]').forEach(button => {
      button.dataset.aiSource = first.dataset.aiSource;
      button.dataset.aiLine = String(lines[0]);
      button.dataset.aiEndLine = String(lines[lines.length - 1]);
      button.dataset.aiLines = JSON.stringify(lines);
      button.dataset.aiLabel = label;
    });
  }

  function clearSelection() {
    selected.clear();
    anchor = null;
    renderSelection();
  }

  function range(first, last) {
    // Cell revisions and source snapshots cannot share a selection.
    if (!sameSource(first, last)) return [last];
    const rows = Array.from(first.closest('tbody').querySelectorAll('.source-row'))
      .filter(row => sameSource(first, row));
    const start = rows.indexOf(first);
    const end = rows.indexOf(last);
    return rows.slice(Math.min(start, end), Math.max(start, end) + 1);
  }

  function extendDrag(row) {
    if (drag.last === row || !sameSource(drag.origin, row)) return;
    drag.last = row;
    selected.clear();
    drag.base.forEach(item => selected.add(item));
    for (const item of range(drag.origin, row)) {
      if (drag.remove) selected.delete(item);
      else selected.add(item);
    }
    renderSelection();
  }

  document.querySelectorAll('.source-row .line-number a').forEach(link => {
    link.title = 'Select line · Shift: range · Ctrl/Cmd: toggle · Drag: select lines';
    link.setAttribute('role', 'button');
    link.setAttribute('aria-pressed', 'false');
  });
  document.addEventListener('pointerdown', event => {
    suppressClick = false;
    if (event.button !== 0 || event.altKey || document.querySelector('.ai-dialog[open]')) return;
    const row = event.target.closest('.source-row');
    if (!row || event.target.closest('button, input, select, textarea')) return;
    const gutter = event.target.closest('.line-number');
    if (!gutter && (event.pointerType === 'touch' || event.target.closest('a') && !event.shiftKey && !event.ctrlKey && !event.metaKey)) return;
    // Own row gestures before the browser starts selecting code and metrics.
    // Alt-drag still allows ordinary text copying; symbol links stay usable.
    event.preventDefault();
    window.getSelection()?.removeAllRanges();
    const additive = event.ctrlKey || event.metaKey;
    if (!sameSource(anchor, row)) anchor = null;
    if (selected.size && !sameSource(selected.values().next().value, row)) selected.clear();
    const origin = event.shiftKey && anchor ? anchor : row;
    drag = {
      origin,
      last: null,
      pointerId: event.pointerId,
      base: additive ? new Set(selected) : new Set(),
      remove: additive && !event.shiftKey && selected.has(row),
    };
    if (!event.shiftKey || !anchor) anchor = row;
    suppressClick = true;
    document.body.classList.add('ai-selecting');
    extendDrag(row);
  });
  document.addEventListener('pointermove', event => {
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (!(event.buttons & 1)) {
      finishDrag();
      return;
    }
    const row = event.target.closest('.source-row');
    if (row) extendDrag(row);
  });
  function finishDrag(event) {
    drag = null;
    document.body.classList.remove('ai-selecting');
    if (event?.type !== 'pointerup') suppressClick = false;
  }
  document.addEventListener('pointerup', finishDrag);
  document.addEventListener('pointercancel', finishDrag);
  window.addEventListener('blur', finishDrag);
  function selectWithKeyboard(row, event) {
    event.preventDefault();
    event.stopPropagation();
    if (!sameSource(anchor, row)) anchor = null;
    if ((!event.ctrlKey && !event.metaKey) || !anchor) selected.clear();
    for (const item of range(event.shiftKey && anchor ? anchor : row, row)) {
      if ((event.ctrlKey || event.metaKey) && !event.shiftKey && selected.has(item)) selected.delete(item);
      else selected.add(item);
    }
    if (!event.shiftKey || !anchor) anchor = row;
    renderSelection();
  }
  document.addEventListener('click', event => {
    const suppress = suppressClick;
    suppressClick = false;
    if (suppress && event.detail > 0) {
      event.preventDefault();
      event.stopPropagation();
      return;
    }
    // Enter on a line number offers the same selection as a pointer click.
    const link = event.target.closest('.source-row .line-number a');
    if (link && event.detail === 0) {
      selectWithKeyboard(link.closest('.source-row'), event);
    }
  }, true);
  document.getElementById('ai-selection-clear').addEventListener('click', clearSelection);
  document.addEventListener('keydown', event => {
    const link = event.target.closest('.source-row .line-number a');
    if (event.key === ' ' && link) selectWithKeyboard(link.closest('.source-row'), event);
    if (event.key === 'Escape' && !document.querySelector('.ai-dialog[open]')) {
      finishDrag();
      clearSelection();
    }
  });
  window.addEventListener('hashchange', () => {
    finishDrag();
    clearSelection();
  });
})();
