(() => {
  'use strict';
  const pages = Array.from(document.querySelectorAll('.page'));
  const navigation = Array.from(document.querySelectorAll('.nav-link'));
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
      const top = scroller.scrollTop + target.getBoundingClientRect().top - scroller.getBoundingClientRect().top - scroller.clientTop - headerHeight;
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
    const group = selected.id.startsWith('source-') ? 'files' : selected.id.startsWith('spark-') ? 'spark' : selected.id.startsWith('run-') ? 'notebooks' : selected.id;
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
  document.querySelectorAll('.source-order').forEach(button => {
    button.addEventListener('click', () => {
      const page = button.closest('.source-page');
      const body = page.querySelector('.source-table tbody');
      const order = button.dataset.order;
      const rows = Array.from(body.querySelectorAll('.source-row'));
      rows.sort((left, right) => {
        const lineOrder = Number(left.dataset.line) - Number(right.dataset.line);
        if (order === 'line') return lineOrder;
        const leftValue = left.dataset[order];
        const rightValue = right.dataset[order];
        // Missing measurements sort after every known value, including zero and frees.
        if (leftValue === '' || rightValue === '') {
          return (leftValue === '') - (rightValue === '') || lineOrder;
        }
        return Number(rightValue) - Number(leftValue) || lineOrder;
      });
      rows.forEach(row => body.appendChild(row));
      page.querySelectorAll('.source-order').forEach(control => {
        control.setAttribute('aria-pressed', String(control === button));
      });
      const scroller = page.querySelector('.source-scroll');
      scroller.style.removeProperty('--source-tail-space');
      scroller.scrollTop = 0;
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
  document.querySelectorAll('.memory-cursor').forEach(cursor => {
    cursor.addEventListener('input', () => {
      const inspector = cursor.closest('.memory-inspector');
      const point = inspector.querySelector(`.memory-point[data-index="${cursor.value}"]`);
      inspector.querySelectorAll('.memory-point.selected').forEach(item => item.classList.remove('selected'));
      const output = inspector.querySelector('.memory-reading');
      if (!point) {
        output.textContent = 'RAM reading unavailable at this observation';
        return;
      }
      point.classList.add('selected');
      output.textContent = point.dataset.label;
      const source = point.closest('a');
      if (source) {
        output.appendChild(document.createTextNode(' · '));
        const link = document.createElement('a');
        link.setAttribute('href', source.getAttribute('href'));
        link.textContent = 'Open source';
        output.appendChild(link);
      }
    });
  });
  route();
})();
