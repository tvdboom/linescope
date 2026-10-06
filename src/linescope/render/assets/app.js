(() => {
  'use strict';
  const pages = Array.from(document.querySelectorAll('.page'));
  const navigation = Array.from(document.querySelectorAll('.nav-link'));
  const sourceHeaderResize = new ResizeObserver(entries => {
    entries.forEach(entry => {
      const height = entry.target.getBoundingClientRect().height;
      entry.target.closest('.source-scroll').style.setProperty('--source-head-height', height + 'px');
    });
  });
  document.querySelectorAll('.source-table thead').forEach(header => sourceHeaderResize.observe(header));
  function route() {
    const hash = window.location.hash.slice(1) || 'overview';
    const target = document.getElementById(hash);
    const page = target && (target.classList.contains('page') ? target : target.closest('.page'));
    const selected = page || document.getElementById('overview');
    pages.forEach(item => { item.hidden = item !== selected; });
    document.querySelectorAll('.source-row.selected').forEach(row => {
      row.classList.remove('selected');
      row.closest('.source-scroll').style.removeProperty('--source-tail-space');
    });
    document.querySelectorAll('.spark-operator.selected').forEach(operator => {
      operator.classList.remove('selected');
    });
    if (target && target.classList.contains('source-row')) {
      target.classList.add('selected');
      const scroller = target.closest('.source-scroll');
      const headerHeight = scroller.querySelector('thead').getBoundingClientRect().height;
      scroller.style.setProperty('--source-head-height', headerHeight + 'px');
      const cellHeight = target.closest('tbody').querySelector('.source-cell-heading')?.getBoundingClientRect().height || 0;
      const top = scroller.scrollTop + target.getBoundingClientRect().top - scroller.getBoundingClientRect().top - scroller.clientTop - headerHeight - cellHeight;
      // Allow definitions near the end of a file to sit directly below the header.
      const tableHeight = scroller.querySelector('.source-table').getBoundingClientRect().height;
      const tailSpace = Math.max(0, top + scroller.clientHeight - tableHeight);
      scroller.style.setProperty('--source-tail-space', tailSpace + 'px');
      scroller.scrollTop = top;
    }
    if (target && target.classList.contains('spark-operator')) {
      target.classList.add('selected');
      // Open the selected step and every containing disclosure before scrolling.
      for (let ancestor = target; ancestor && ancestor !== selected; ancestor = ancestor.parentElement) {
        if (ancestor.tagName === 'DETAILS') ancestor.open = true;
      }
      target.scrollIntoView({ block: 'start' });
      target.querySelector('summary').focus({ preventScroll: true });
    }
    window.scrollTo(0, 0);
    const group = selected.id.startsWith('source-') || selected.id.startsWith('run-') ? 'files' : selected.id.startsWith('spark-') ? 'spark' : selected.id;
    navigation.forEach(item => {
      if (item.getAttribute('href') === '#' + group) item.setAttribute('aria-current', 'page');
      else item.removeAttribute('aria-current');
    });
    document.title = 'LineScope · ' + (selected.querySelector('h1')?.textContent || 'Source profile');
  }
  window.addEventListener('hashchange', route);
  document.addEventListener('click', event => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href^="#"]');
    // A repeated link click must restore alignment after manual scrolling.
    if (link && link.getAttribute('href') === window.location.hash) {
      event.preventDefault();
      route();
    }
  });
  document.getElementById('source-search').addEventListener('input', event => {
    const query = event.target.value.toLocaleLowerCase();
    document.querySelectorAll('.source-item').forEach(item => {
      item.hidden = !item.getAttribute('title').toLocaleLowerCase().includes(query);
    });
  });
  document.getElementById('theme-toggle').addEventListener('click', () => {
    const dark = document.body.classList.contains('dark') || (!document.body.classList.contains('light') && window.matchMedia('(prefers-color-scheme: dark)').matches);
    document.body.classList.toggle('dark', !dark);
    document.body.classList.toggle('light', dark);
  });
  document.querySelectorAll('.plan-tab').forEach(button => {
    button.addEventListener('click', () => {
      const page = button.closest('.page');
      page.querySelectorAll('.plan-tab').forEach(tab => tab.setAttribute('aria-pressed', String(tab === button)));
      page.querySelectorAll('.plan-view').forEach(panel => { panel.hidden = panel.dataset.tabPanel !== button.dataset.tab; });
    });
  });
  document.querySelectorAll('.source-heat').forEach(button => {
    button.addEventListener('click', () => {
      const page = button.closest('.page');
      const metric = button.dataset.heat === 'memory' ? 'heatMemory' : 'heatTime';
      page.querySelectorAll('.heat-row').forEach(row => {
        row.style.setProperty('--heat', button.dataset.heat === 'none' ? '0' : row.dataset[metric]);
      });
      page.querySelectorAll('.source-heat').forEach(control => {
        control.setAttribute('aria-pressed', String(control === button));
      });
    });
  });
  document.querySelectorAll('.sortable-table').forEach(table => {
    const bodies = Array.from(table.tBodies);
    const sortableRows = body => Array.from(body.rows).filter(row => !row.classList.contains('source-cell-heading'));
    const originalOrder = new Map(bodies.flatMap(sortableRows).map((row, index) => [row, index]));
    const sorters = Array.from(table.querySelectorAll('.table-sort'));
    function updateSortState(button, direction) {
      sorters.forEach(control => {
        const column = control.closest('th');
        if (control === button) column.setAttribute('aria-sort', direction);
        else column.removeAttribute('aria-sort');
        const nextDirection = control === button ? (direction === 'ascending' ? 'descending' : 'ascending') : control.dataset.sortDirection;
        control.setAttribute('aria-label', 'Sort by ' + control.dataset.sortLabel + ', ' + nextDirection);
      });
      const scroller = table.closest('.source-scroll, .table-scroll');
      scroller.style.removeProperty('--source-tail-space');
      scroller.scrollTop = 0;
    }
    sorters.forEach(button => {
      button.addEventListener('click', () => {
        const header = button.closest('th');
        const current = header.getAttribute('aria-sort');
        const direction = current ? (current === 'ascending' ? 'descending' : 'ascending') : button.dataset.sortDirection;
        const multiplier = direction === 'ascending' ? 1 : -1;
        // Notebook cells retain their boundaries and capture order when sorting.
        bodies.forEach(body => {
          const rows = sortableRows(body);
          rows.sort((left, right) => {
            const tie = originalOrder.get(left) - originalOrder.get(right);
            const leftValue = left.getAttribute('data-' + button.dataset.sort) ?? '';
            const rightValue = right.getAttribute('data-' + button.dataset.sort) ?? '';
            // Missing measurements stay last in both directions, even beside zero or frees.
            if (leftValue === '' || rightValue === '') {
              return (leftValue === '') - (rightValue === '') || tie;
            }
            const comparison = button.dataset.sortType === 'number' ? Number(leftValue) - Number(rightValue) : leftValue.localeCompare(rightValue, undefined, { sensitivity: 'base' });
            return comparison * multiplier || tie;
          });
          rows.forEach(row => body.appendChild(row));
        });
        updateSortState(button, direction);
      });
    });
  });
  document.querySelectorAll('.spark-order').forEach(button => {
    button.addEventListener('click', () => {
      const section = button.closest('.spark-cost-section');
      const body = section.querySelector('tbody');
      const rows = Array.from(body.querySelectorAll('tr'));
      const order = button.dataset.order;
      rows.sort((left, right) => {
        const leftValue = left.dataset[order];
        const rightValue = right.dataset[order];
        // An unavailable counter must stay below measured zeroes.
        if (leftValue === '' || rightValue === '') {
          return (leftValue === '') - (rightValue === '');
        }
        return Number(rightValue) - Number(leftValue);
      });
      rows.forEach(row => body.appendChild(row));
      section.querySelectorAll('.spark-order').forEach(control => {
        control.setAttribute('aria-pressed', String(control === button));
      });
      section.querySelector('.table-scroll').scrollTop = 0;
    });
  });
  document.getElementById('spark-action-select')?.addEventListener('change', event => {
    document.querySelectorAll('.spark-action-overview').forEach(panel => {
      panel.hidden = panel.dataset.action !== event.target.value;
    });
  });
  document.querySelectorAll('.memory-chart').forEach(chart => {
    const inspector = chart.closest('.memory-inspector');
    const scroller = chart.closest('.memory-chart-scroll');
    const tooltip = inspector.querySelector('.memory-tooltip');
    const guide = chart.querySelector('.memory-guide');
    const observations = Array.from(chart.querySelectorAll('.memory-observation'));
    const positions = observations.map(item => Number(item.dataset.x));
    let selectedIndex = Number(chart.dataset.initialIndex);
    let selectedPoint = null;

    function hideReading() {
      tooltip.hidden = true;
      inspector.classList.remove('is-inspecting');
      selectedPoint?.classList.remove('selected');
      selectedPoint = null;
    }

    function placeTooltip() {
      const observation = observations[selectedIndex];
      const point = observation.querySelector('.memory-point');
      const transform = chart.getScreenCTM();
      if (!transform) return;
      const position = new DOMPoint(positions[selectedIndex], point ? Number(point.getAttribute('cy')) : 147.5).matrixTransform(transform);
      const bounds = scroller.getBoundingClientRect();
      const x = position.x - bounds.left + scroller.scrollLeft;
      const y = position.y - bounds.top + scroller.scrollTop;
      const width = tooltip.offsetWidth;
      const height = tooltip.offsetHeight;
      const left = Math.max(scroller.scrollLeft + 8, Math.min(x - width / 2, scroller.scrollLeft + scroller.clientWidth - width - 8));
      const top = y - height - 12 >= 8 ? y - height - 12 : y + 12;
      tooltip.style.left = left + 'px';
      tooltip.style.top = Math.max(8, Math.min(top, scroller.clientHeight - height - 8)) + 'px';
    }

    function showReading(index) {
      const observation = observations[index];
      if (selectedIndex !== index || tooltip.hidden) {
        selectedPoint?.classList.remove('selected');
        selectedIndex = index;
        selectedPoint = observation.querySelector('.memory-point');
        selectedPoint?.classList.add('selected');
        tooltip.replaceChildren();
        for (const [label, value] of [['Time', observation.dataset.time], ['RAM', observation.dataset.ram]]) {
          const row = document.createElement('div');
          const name = document.createElement('span');
          const reading = document.createElement('strong');
          name.textContent = label;
          reading.textContent = value;
          row.append(name, reading);
          tooltip.appendChild(row);
        }
        const source = observation.querySelector('a');
        if (source) {
          const link = document.createElement('a');
          link.setAttribute('href', source.getAttribute('href'));
          link.textContent = source.dataset.sourceLabel;
          tooltip.appendChild(link);
        }
        guide.setAttribute('x1', observation.dataset.x);
        guide.setAttribute('x2', observation.dataset.x);
        tooltip.hidden = false;
        inspector.classList.add('is-inspecting');
      }
      placeTooltip();
    }

    scroller.addEventListener('pointermove', event => {
      // Keep the source link usable while the pointer is over its tooltip.
      if (tooltip.contains(event.target)) return;
      const transform = chart.getScreenCTM();
      if (!transform) return;
      const position = new DOMPoint(event.clientX, event.clientY).matrixTransform(transform.inverse());
      if (position.x < 125 || position.x > 960 || position.y < 35 || position.y > 260) {
        hideReading();
        return;
      }
      // Search retained observations rather than inventing interpolated RAM.
      let low = 0;
      let high = positions.length - 1;
      while (low < high) {
        const middle = Math.floor((low + high) / 2);
        if (positions[middle] < position.x) low = middle + 1;
        else high = middle;
      }
      const index = low > 0 && position.x - positions[low - 1] <= positions[low] - position.x ? low - 1 : low;
      showReading(index);
    });
    scroller.addEventListener('pointerleave', hideReading);
    scroller.addEventListener('focusout', event => {
      if (!scroller.contains(event.relatedTarget)) hideReading();
    });
    scroller.addEventListener('scroll', hideReading);
    chart.addEventListener('focus', () => showReading(selectedIndex));
    chart.addEventListener('keydown', event => {
      if (event.ctrlKey || event.altKey || event.metaKey) return;
      let index = selectedIndex;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') index--;
      else if (event.key === 'ArrowRight' || event.key === 'ArrowUp') index++;
      else if (event.key === 'Home') index = 0;
      else if (event.key === 'End') index = observations.length - 1;
      else if (event.key === 'Escape') hideReading();
      else if (event.key === 'Enter') observations[index].querySelector('a')?.click();
      else return;
      event.preventDefault();
      if (event.key !== 'Escape' && event.key !== 'Enter') showReading(Math.max(0, Math.min(index, observations.length - 1)));
    });
    window.addEventListener('resize', hideReading);
    window.addEventListener('hashchange', hideReading);
  });
  route();
})();
