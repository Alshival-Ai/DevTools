(function () {
  const GH_EMPTY = '<p class="text-muted">No content yet.</p>';
  const GH_PREVIEW_EMPTY = '<p class="text-muted">Start writing markdown to preview your page.</p>';
  const state = {
    mermaidTheme: '',
    mermaidReady: false,
  };

  const escapeHtml = (value) => {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  };

  const fallbackRender = (source) => {
    return escapeHtml(source).replace(/\n/g, '<br>');
  };

  const sanitizeHtml = (unsafeHtml) => {
    if (!window.DOMPurify || typeof window.DOMPurify.sanitize !== 'function') {
      return unsafeHtml;
    }
    return window.DOMPurify.sanitize(unsafeHtml, {
      ADD_TAGS: ['details', 'summary', 'kbd', 'samp', 'sub', 'sup', 'mark', 'ins', 'del'],
      ADD_ATTR: ['target', 'rel', 'class', 'id', 'name', 'align'],
      ALLOW_DATA_ATTR: false,
      FORBID_TAGS: ['style', 'script'],
    });
  };

  const configureMarked = () => {
    if (!window.marked || typeof window.marked.parse !== 'function') {
      return false;
    }
    if (window.marked._alshivalConfigured) {
      return true;
    }
    if (typeof window.marked.setOptions === 'function') {
      const renderer = typeof window.marked.Renderer === 'function' ? new window.marked.Renderer() : null;
      if (renderer && typeof renderer.code === 'function') {
        const originalCode = renderer.code.bind(renderer);
        renderer.code = function (...args) {
          const token = args.length === 1 && typeof args[0] === 'object'
            ? (args[0] || {})
            : {
                text: args[0] || '',
                lang: args[1] || '',
                escaped: !!args[2],
              };
          const lang = String(token.lang || '').trim().toLowerCase().split(/\s+/)[0];
          if (lang === 'mermaid') {
            return `<pre class="mermaid">${escapeHtml(token.text || '')}</pre>`;
          }
          return originalCode(...args);
        };
      }

      window.marked.setOptions({
        gfm: true,
        breaks: false,
        mangle: false,
        renderer: renderer || undefined,
      });
    }
    window.marked._alshivalConfigured = true;
    return true;
  };

  const normalizeLinks = (root) => {
    const anchors = root.querySelectorAll('a[href]');
    anchors.forEach((anchor) => {
      const href = String(anchor.getAttribute('href') || '').trim().toLowerCase();
      const external = href && !href.startsWith('#') && !href.startsWith('/') && !href.startsWith('mailto:') && !href.startsWith('tel:');
      if (!external) return;
      anchor.setAttribute('target', '_blank');
      anchor.setAttribute('rel', 'noopener noreferrer');
    });
  };

  const desiredMermaidTheme = () => {
    return document.documentElement.classList.contains('light-style') ? 'default' : 'dark';
  };

  const ensureMermaid = () => {
    if (!window.mermaid || typeof window.mermaid.initialize !== 'function') {
      return false;
    }
    const theme = desiredMermaidTheme();
    if (!state.mermaidReady || state.mermaidTheme !== theme) {
      window.mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme,
      });
      state.mermaidReady = true;
      state.mermaidTheme = theme;
    }
    return true;
  };

  const renderMermaid = async (root) => {
    const preBlocks = Array.from(root.querySelectorAll('pre.mermaid'));
    preBlocks.forEach((block) => {
      const container = document.createElement('div');
      container.className = 'mermaid';
      container.textContent = block.textContent || '';
      block.replaceWith(container);
    });

    const nodes = Array.from(root.querySelectorAll('.mermaid'));
    if (!nodes.length || !ensureMermaid() || typeof window.mermaid.run !== 'function') {
      return;
    }
    nodes.forEach((node, index) => {
      if (!node.id) {
        node.id = `wiki-mermaid-${Date.now()}-${index}`;
      }
    });

    try {
      await window.mermaid.run({ nodes });
    } catch (error) {
      // Keep markdown visible if Mermaid fails to parse.
    }
  };

  const renderMarkdown = (value, emptyStateHtml) => {
    const source = String(value || '');
    if (!source.trim()) {
      return emptyStateHtml || GH_EMPTY;
    }

    if (!configureMarked()) {
      return fallbackRender(source);
    }

    const rendered = window.marked.parse(source);
    return sanitizeHtml(rendered);
  };

  const renderInto = async (target, value, emptyStateHtml) => {
    if (!target) return;
    target.innerHTML = renderMarkdown(value, emptyStateHtml);
    normalizeLinks(target);
    await renderMermaid(target);
  };

  window.AlshivalWikiMarkdown = {
    GH_EMPTY,
    GH_PREVIEW_EMPTY,
    renderMarkdown,
    renderInto,
  };
})();
