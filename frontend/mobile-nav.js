// Keep basic navigation available while the app and PDF viewer are loading.
(() => {
  const nav = document.getElementById('mobile-nav-dialog');
  const buttons = document.querySelectorAll('[data-mobile-menu]');
  const mobile = window.matchMedia('(max-width: 600px)');
  const expanded = value => buttons.forEach(button => button.setAttribute('aria-expanded', String(value)));
  const close = () => {
    if (nav.open) nav.close();
    expanded(false);
  };

  buttons.forEach(button => button.addEventListener('click', () => {
    if (!mobile.matches) return;
    if (nav.open) { close(); return; }
    nav.showModal();
    expanded(true);
  }));
  nav.querySelector('[data-mobile-close]').addEventListener('click', close);
  nav.querySelectorAll('a').forEach(link => link.addEventListener('click', close));
  nav.addEventListener('cancel', event => { event.preventDefault(); close(); });
  nav.addEventListener('close', () => expanded(nav.open));
  nav.addEventListener('click', event => {
    const rect = nav.getBoundingClientRect();
    if (event.target === nav && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) close();
  });
  window.addEventListener('hashchange', close);
  mobile.addEventListener('change', close);
})();
