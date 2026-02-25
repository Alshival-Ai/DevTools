(function () {
  const appShell = document.querySelector('.app-shell');
  if (!appShell) return;

  const toggle = appShell.querySelector('[data-sidebar-toggle]');
  const backdrop = appShell.querySelector('[data-sidebar-backdrop]');
  const navLinks = appShell.querySelectorAll('.sidenav .nav-item');
  const mobileQuery = window.matchMedia('(max-width: 980px)');

  const isOpen = () => appShell.classList.contains('sidebar-open');

  const setOpen = (open) => {
    appShell.classList.toggle('sidebar-open', open);
    if (toggle) {
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    document.body.classList.toggle('sidebar-open', open);
  };

  toggle?.addEventListener('click', () => {
    setOpen(!isOpen());
  });

  backdrop?.addEventListener('click', () => {
    setOpen(false);
  });

  navLinks.forEach((link) => {
    link.addEventListener('click', () => {
      if (mobileQuery.matches) {
        setOpen(false);
      }
    });
  });

  const handleViewport = (event) => {
    if (!event.matches) {
      setOpen(false);
    }
  };

  if (typeof mobileQuery.addEventListener === 'function') {
    mobileQuery.addEventListener('change', handleViewport);
  } else if (typeof mobileQuery.addListener === 'function') {
    mobileQuery.addListener(handleViewport);
  }
})();
