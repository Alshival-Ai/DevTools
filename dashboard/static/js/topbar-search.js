(() => {
  const searchRoot = document.querySelector('[data-topbar-search]');
  if (!searchRoot) {
    return;
  }

  const input = searchRoot.querySelector('[data-topbar-search-input]');
  const dropdown = searchRoot.querySelector('[data-topbar-search-dropdown]');
  const resultsList = searchRoot.querySelector('[data-topbar-search-results]');
  const emptyState = searchRoot.querySelector('[data-topbar-search-empty]');
  const searchUrl = searchRoot.getAttribute('data-search-url') || '';
  const contextResourceUuid = String(searchRoot.getAttribute('data-resource-uuid') || '').trim().toLowerCase();
  if (!input || !dropdown || !resultsList || !emptyState || !searchUrl) {
    return;
  }

  let isOpen = false;
  let debounceTimer = null;
  let requestToken = 0;
  let lastQuery = '';
  let suggestions = [];
  let activeIndex = -1;

  function normalizeQuery(value) {
    return String(value || '').replace(/\s+/g, ' ').trim();
  }

  function setOpen(nextOpen) {
    isOpen = !!nextOpen;
    dropdown.hidden = !isOpen;
    searchRoot.classList.toggle('is-open', isOpen);
  }

  function clearResults() {
    while (resultsList.firstChild) {
      resultsList.removeChild(resultsList.firstChild);
    }
    suggestions = [];
    activeIndex = -1;
  }

  function setEmptyMessage(message) {
    emptyState.textContent = String(message || '').trim();
    emptyState.hidden = false;
  }

  function updateActiveSelection() {
    const nodes = resultsList.querySelectorAll('[data-topbar-search-item]');
    nodes.forEach((node, index) => {
      node.classList.toggle('is-active', index === activeIndex);
    });
  }

  function setActiveIndex(nextIndex) {
    if (!Array.isArray(suggestions) || suggestions.length === 0) {
      activeIndex = -1;
      updateActiveSelection();
      return;
    }
    const maxIndex = suggestions.length - 1;
    const resolved = Math.max(0, Math.min(maxIndex, Number(nextIndex) || 0));
    activeIndex = resolved;
    updateActiveSelection();
  }

  function kindLabel(kind) {
    const resolved = String(kind || '').toLowerCase();
    if (resolved === 'resource') return 'Resource';
    if (resolved === 'wiki') return 'Wiki';
    return 'KB';
  }

  function renderSuggestions(items) {
    clearResults();
    const normalizedItems = Array.isArray(items) ? items : [];
    suggestions = normalizedItems
      .map((item) => {
        if (!item || typeof item !== 'object') return null;
        const url = String(item.url || '').trim();
        const title = String(item.title || '').trim();
        if (!url || !title) return null;
        return {
          kind: String(item.kind || 'kb').toLowerCase(),
          title,
          subtitle: String(item.subtitle || '').trim(),
          snippet: String(item.snippet || '').trim(),
          url,
        };
      })
      .filter(Boolean);

    if (suggestions.length === 0) {
      setEmptyMessage('No matches found.');
      return;
    }

    emptyState.hidden = true;
    suggestions.forEach((item, index) => {
      const link = document.createElement('a');
      link.className = 'topbar-search-item';
      link.href = item.url;
      link.setAttribute('data-topbar-search-item', String(index));

      const meta = document.createElement('div');
      meta.className = 'topbar-search-item__meta';

      const kind = document.createElement('span');
      kind.className = `topbar-search-kind topbar-search-kind--${item.kind}`;
      kind.textContent = kindLabel(item.kind);

      const subtitle = document.createElement('span');
      subtitle.className = 'topbar-search-item__subtitle';
      subtitle.textContent = item.subtitle;

      meta.appendChild(kind);
      if (item.subtitle) {
        meta.appendChild(subtitle);
      }

      const title = document.createElement('div');
      title.className = 'topbar-search-item__title';
      title.textContent = item.title;

      const snippet = document.createElement('div');
      snippet.className = 'topbar-search-item__snippet';
      snippet.textContent = item.snippet;

      link.appendChild(meta);
      link.appendChild(title);
      if (item.snippet) {
        link.appendChild(snippet);
      }

      resultsList.appendChild(link);
    });
  }

  async function runSearch(query) {
    const token = ++requestToken;
    lastQuery = query;
    clearResults();
    setEmptyMessage('Searching...');
    setOpen(true);

    try {
      const params = new URLSearchParams({
        q: query,
        limit: '8',
      });
      if (contextResourceUuid) {
        params.set('context_resource_uuid', contextResourceUuid);
      }
      const response = await fetch(
        `${searchUrl}?${params.toString()}`,
        {
          method: 'GET',
          credentials: 'same-origin',
        }
      );
      if (!response.ok) {
        throw new Error(`kb_search_${response.status}`);
      }
      const payload = await response.json().catch(() => ({}));
      if (token !== requestToken) {
        return;
      }
      const items = payload && Array.isArray(payload.results) ? payload.results : [];
      renderSuggestions(items);
      setOpen(true);
    } catch (error) {
      if (token !== requestToken) {
        return;
      }
      clearResults();
      setEmptyMessage('Search unavailable right now.');
      setOpen(true);
    }
  }

  function queueSearch() {
    const query = normalizeQuery(input.value);
    if (debounceTimer) {
      window.clearTimeout(debounceTimer);
      debounceTimer = null;
    }

    if (query.length < 2) {
      requestToken += 1;
      lastQuery = query;
      clearResults();
      setEmptyMessage('Type at least 2 characters.');
      setOpen(false);
      return;
    }

    debounceTimer = window.setTimeout(() => {
      void runSearch(query);
    }, 180);
  }

  input.addEventListener('input', queueSearch);
  input.addEventListener('focus', () => {
    const query = normalizeQuery(input.value);
    if (query.length < 2) {
      return;
    }
    if (query === lastQuery && suggestions.length > 0) {
      setOpen(true);
      return;
    }
    queueSearch();
  });

  input.addEventListener('keydown', (event) => {
    if (!isOpen && (event.key === 'ArrowDown' || event.key === 'ArrowUp') && suggestions.length > 0) {
      setOpen(true);
    }
    if (event.key === 'Escape' && isOpen) {
      event.preventDefault();
      setOpen(false);
      return;
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (suggestions.length === 0) {
        return;
      }
      event.preventDefault();
      const delta = event.key === 'ArrowDown' ? 1 : -1;
      const nextIndex = activeIndex < 0
        ? (delta > 0 ? 0 : suggestions.length - 1)
        : (activeIndex + delta + suggestions.length) % suggestions.length;
      setActiveIndex(nextIndex);
      return;
    }
    if (event.key === 'Enter' && suggestions.length > 0) {
      const resolvedIndex = activeIndex >= 0 ? activeIndex : 0;
      const active = suggestions[resolvedIndex];
      if (active && active.url) {
        event.preventDefault();
        window.location.assign(active.url);
      }
    }
  });

  resultsList.addEventListener('mousemove', (event) => {
    if (!(event.target instanceof Element)) {
      return;
    }
    const itemNode = event.target.closest('[data-topbar-search-item]');
    if (!itemNode) {
      return;
    }
    const index = Number(itemNode.getAttribute('data-topbar-search-item'));
    if (!Number.isFinite(index)) {
      return;
    }
    if (index !== activeIndex) {
      setActiveIndex(index);
    }
  });

  document.addEventListener('click', (event) => {
    if (!isOpen) {
      return;
    }
    if (!searchRoot.contains(event.target)) {
      setOpen(false);
    }
  });
})();
