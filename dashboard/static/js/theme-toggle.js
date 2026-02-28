(function () {
  const storageKey = 'alshival-theme';
  const root = document.documentElement;
  const toggles = document.querySelectorAll('.theme__toggle');
  const prefersDark = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  const getStoredTheme = () => {
    try {
      const stored = localStorage.getItem(storageKey);
      if (stored === 'dark' || stored === 'light') {
        return stored;
      }
    } catch (error) {
      return null;
    }
    return null;
  };

  const setStoredTheme = (mode) => {
    try {
      localStorage.setItem(storageKey, mode);
    } catch (error) {
      // Ignore storage errors (private mode or blocked storage).
    }
  };

  const applyTheme = (mode) => {
    root.classList.toggle('light-style', mode === 'light');
    root.dataset.themeMode = mode;
    root.style.colorScheme = mode;
  };

  const stored = getStoredTheme();
  const initial =
    root.dataset.themeMode === 'dark' || root.dataset.themeMode === 'light'
      ? root.dataset.themeMode
      : stored || (prefersDark && prefersDark.matches ? 'dark' : 'light');
  applyTheme(initial);

  toggles.forEach((toggle) => {
    toggle.checked = initial === 'dark';
    toggle.addEventListener('change', () => {
      const next = toggle.checked ? 'dark' : 'light';
      applyTheme(next);
      setStoredTheme(next);
      toggles.forEach((other) => {
        if (other !== toggle) {
          other.checked = toggle.checked;
        }
      });
    });
  });

  if (prefersDark && !stored) {
    const handleChange = (event) => {
      const mode = event.matches ? 'dark' : 'light';
      applyTheme(mode);
      toggles.forEach((toggle) => {
        toggle.checked = mode === 'dark';
      });
    };
    if (typeof prefersDark.addEventListener === 'function') {
      prefersDark.addEventListener('change', handleChange);
    } else if (typeof prefersDark.addListener === 'function') {
      prefersDark.addListener(handleChange);
    }
  }
})();
