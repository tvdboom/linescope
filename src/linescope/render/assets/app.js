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
    document.querySelectorAll('.source-row.selected').forEach(row => row.classList.remove('selected'));
    if (target && target.classList.contains('source-row')) {
      target.classList.add('selected');
      const scroller = target.closest('.source-scroll');
      scroller.scrollTop += target.getBoundingClientRect().top - scroller.getBoundingClientRect().top - scroller.clientHeight / 2 + target.clientHeight / 2;
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
  route();
})();
