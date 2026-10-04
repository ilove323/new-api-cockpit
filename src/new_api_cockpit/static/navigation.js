(() => {
  'use strict';
  const button = document.getElementById('sidebar-toggle');
  if (!button) return;
  const backdrop = document.getElementById('sidebar-backdrop');
  const media = window.matchMedia('(max-width:700px)');
  const render = () => {
    const expanded = media.matches
      ? document.body.classList.contains('sidebar-expanded')
      : !document.body.classList.contains('sidebar-collapsed');
    const label = expanded ? '收起导航' : '展开导航';
    button.setAttribute('aria-expanded', String(expanded));
    button.setAttribute('aria-label', label);
    button.title = label;
    if (backdrop) backdrop.hidden = !media.matches || !expanded;
  };
  try {
    if (localStorage.getItem('navigation-collapsed') === '1') document.body.classList.add('sidebar-collapsed');
  } catch { /* Storage may be blocked; navigation remains usable. */ }
  button.addEventListener('click', () => {
    if (media.matches) document.body.classList.toggle('sidebar-expanded');
    else {
      document.body.classList.toggle('sidebar-collapsed');
      try {
        localStorage.setItem('navigation-collapsed', document.body.classList.contains('sidebar-collapsed') ? '1' : '0');
      } catch { /* localStorage is not a prerequisite for navigation. */ }
    }
    render();
  });
  const close = () => {
    document.body.classList.remove('sidebar-expanded');
    render();
    button.focus();
  };
  backdrop?.addEventListener('click', close);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && media.matches && document.body.classList.contains('sidebar-expanded')) close();
  });
  media.addEventListener('change', () => {
    document.body.classList.remove('sidebar-expanded');
    render();
  });
  render();
})();
