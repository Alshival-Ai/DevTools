(function () {
  const DEFAULT_WS_PATH = '/terminal/ws/';
  const DEFAULT_HINT = 'Ctrl/Cmd+C copy selection, Ctrl/Cmd+click open links, Ctrl+Shift+V paste, Ctrl+/- zoom';
  const DEFAULT_FEATURES = 'width=1100,height=760,resizable=yes,scrollbars=no';
  const FONT_PREF_KEY = 'devtools_terminal_font_size';
  const ASK_CHAT_ENDPOINT = '/chat/ask/';

  let xtermLoaderPromise = null;
  let askWidget = null;
  let askClient = null;
  let askWidgetDragCleanup = null;
  const root = document.body || document.documentElement;
  const isSuperuser = String((root && root.getAttribute('data-superuser')) || '0') === '1';

  const escapeHtml = (value) => String(value || '').replace(/[&<>"']/g, (char) => {
    if (char === '&') return '&amp;';
    if (char === '<') return '&lt;';
    if (char === '>') return '&gt;';
    if (char === '"') return '&quot;';
    return '&#39;';
  });

  const getCookie = (name) => {
    const value = `; ${document.cookie || ''}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length !== 2) return '';
    return parts.pop().split(';').shift() || '';
  };

  const ensureStyle = (href) => {
    if (document.querySelector(`link[data-terminal-popup-css="${href}"]`)) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    link.setAttribute('data-terminal-popup-css', href);
    document.head.appendChild(link);
  };

  const ensureScript = (src) => new Promise((resolve, reject) => {
    if (document.querySelector(`script[data-terminal-popup-js="${src}"]`)) {
      resolve();
      return;
    }
    const script = document.createElement('script');
    script.src = src;
    script.async = true;
    script.setAttribute('data-terminal-popup-js', src);
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.appendChild(script);
  });

  const ensureXterm = () => {
    if (window.Terminal && window.FitAddon && window.FitAddon.FitAddon) {
      return Promise.resolve();
    }
    if (xtermLoaderPromise) return xtermLoaderPromise;

    ensureStyle('https://cdn.jsdelivr.net/npm/xterm/css/xterm.css');
    xtermLoaderPromise = Promise.all([
      ensureScript('https://cdn.jsdelivr.net/npm/xterm/lib/xterm.js'),
      ensureScript('https://cdn.jsdelivr.net/npm/xterm-addon-fit/lib/xterm-addon-fit.js'),
      ensureScript('https://cdn.jsdelivr.net/npm/xterm-addon-web-links/lib/xterm-addon-web-links.js'),
    ]).then(() => undefined);
    return xtermLoaderPromise;
  };

  const clampFontSize = (value) => Math.max(11, Math.min(22, value));

  const loadFontSize = () => {
    try {
      const raw = Number(window.localStorage.getItem(FONT_PREF_KEY));
      if (Number.isFinite(raw)) return clampFontSize(raw);
    } catch (err) {}
    return 14;
  };

  const saveFontSize = (value) => {
    try {
      window.localStorage.setItem(FONT_PREF_KEY, String(clampFontSize(value)));
    } catch (err) {}
  };

  const buildWsPath = (wsPath, query) => {
    const base = String(wsPath || DEFAULT_WS_PATH);
    const params = new URLSearchParams(query || {});
    const q = params.toString();
    if (!q) return base;
    return base + (base.indexOf('?') === -1 ? '?' : '&') + q;
  };

  const shouldActivateTerminalLink = (event) => Boolean(event && (event.ctrlKey || event.metaKey));

  const openTerminalLink = (scopeWindow, uri) => {
    if (!uri) return;
    const activeWindow = scopeWindow && typeof scopeWindow.open === 'function' ? scopeWindow : window;
    const nextWindow = activeWindow.open();
    if (nextWindow) {
      try { nextWindow.opener = null; } catch (err) {}
      nextWindow.location.href = uri;
      return;
    }
    console.warn('Opening link blocked as opener could not be cleared');
  };

  const makeTerminalClient = async ({ container, title, hintText, wsPath, onSocketClose }) => {
    await ensureXterm();

    const wrap = document.createElement('div');
    wrap.className = 'terminal-inline-wrap';
    wrap.innerHTML = `
      <div class="terminal-inline-bar">
        <span class="terminal-inline-meta">
          <span class="terminal-inline-title">${escapeHtml(title)}</span>
          <span class="terminal-inline-hint">${escapeHtml(hintText || DEFAULT_HINT)}</span>
        </span>
        <span class="terminal-inline-status" aria-live="polite"></span>
      </div>
      <div class="terminal-inline-body"></div>
    `;
    container.innerHTML = '';
    container.appendChild(wrap);

    const termEl = wrap.querySelector('.terminal-inline-body');
    const statusEl = wrap.querySelector('.terminal-inline-status');

    const term = new window.Terminal({
      cursorBlink: true,
      cursorStyle: 'block',
      cursorInactiveStyle: 'outline',
      scrollback: 50000,
      fontFamily: '"SFMono-Regular","Menlo","Monaco","Consolas","Liberation Mono","Courier New",monospace',
      fontSize: loadFontSize(),
      theme: { background: '#0a0e12', foreground: '#d7e2f2', cursor: '#6be4a8', selection: 'rgba(107, 228, 168, 0.3)' },
    });

    const fit = new window.FitAddon.FitAddon();
    term.loadAddon(fit);
    if (window.WebLinksAddon && window.WebLinksAddon.WebLinksAddon) {
      term.loadAddon(new window.WebLinksAddon.WebLinksAddon((event, uri) => {
        if (!shouldActivateTerminalLink(event)) return;
        openTerminalLink(window, uri);
      }));
    }
    term.open(termEl);
    fit.fit();

    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(scheme + '://' + window.location.host + wsPath);
    ws.binaryType = 'arraybuffer';
    const socketStartedAt = Date.now();
    let socketOpened = false;

    let statusTimer = null;
    let lastCopiedSelection = '';
    let currentFontSize = Number(term.options.fontSize) || 14;

    const setStatus = (message, isError) => {
      if (!statusEl) return;
      statusEl.textContent = message || '';
      statusEl.classList.toggle('error', Boolean(isError));
      if (statusTimer) window.clearTimeout(statusTimer);
      if (!message) return;
      statusTimer = window.setTimeout(() => {
        statusEl.textContent = '';
        statusEl.classList.remove('error');
      }, isError ? 2500 : 1200);
    };

    const copySelection = async () => {
      const selected = term.getSelection ? term.getSelection() : '';
      if (!selected) return false;
      if (selected === lastCopiedSelection) return true;
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(selected);
          lastCopiedSelection = selected;
          setStatus('Copied', false);
          return true;
        }
      } catch (err) {}
      return false;
    };

    const pasteFromClipboard = async () => {
      if (!(navigator.clipboard && navigator.clipboard.readText)) {
        setStatus('Clipboard read unavailable', true);
        return false;
      }
      try {
        const text = await navigator.clipboard.readText();
        if (!text) {
          setStatus('Clipboard empty', true);
          return false;
        }
        if (ws.readyState !== WebSocket.OPEN) {
          setStatus('Disconnected', true);
          return false;
        }
        ws.send(text);
        setStatus('Pasted', false);
        return true;
      } catch (err) {
        setStatus('Clipboard blocked', true);
        return false;
      }
    };

    const sendResize = () => {
      const dims = fit.proposeDimensions();
      if (!dims || ws.readyState !== WebSocket.OPEN) return;
      ws.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }));
    };

    const setFontSize = (nextSize) => {
      const fontSize = clampFontSize(nextSize);
      if (fontSize === currentFontSize) return;
      currentFontSize = fontSize;
      term.options.fontSize = fontSize;
      saveFontSize(fontSize);
      fit.fit();
      sendResize();
      setStatus(`Font ${String(fontSize)}px`, false);
    };

    ws.addEventListener('open', () => {
      socketOpened = true;
      term.focus();
      sendResize();
      setStatus('Connected', false);
    });

    ws.addEventListener('message', (event) => {
      if (event.data instanceof ArrayBuffer) {
        term.write(new Uint8Array(event.data));
        return;
      }
      term.write(event.data);
    });

    ws.addEventListener('close', () => {
      term.write('\r\n[terminal session closed]\r\n');
      setStatus('Disconnected', true);
      if (typeof onSocketClose === 'function') {
        onSocketClose({
          opened: socketOpened,
          durationMs: Date.now() - socketStartedAt,
        });
      }
    });

    term.attachCustomKeyEventHandler((event) => {
      const key = (event.key || '').toLowerCase();
      const ctrlOrMeta = Boolean(event.ctrlKey || event.metaKey);
      const hasSelection = Boolean(term.hasSelection && term.hasSelection());
      const wantsCopy = hasSelection && ((ctrlOrMeta && key === 'c') || (event.ctrlKey && event.shiftKey && key === 'c'));
      if (wantsCopy) {
        event.preventDefault();
        copySelection();
        return false;
      }
      const wantsPaste = (event.ctrlKey && event.shiftKey && key === 'v') || (event.metaKey && key === 'v') || (event.shiftKey && key === 'insert');
      if (wantsPaste) {
        event.preventDefault();
        pasteFromClipboard();
        return false;
      }
      if (event.ctrlKey && (key === '=' || key === '+')) {
        event.preventDefault();
        setFontSize(currentFontSize + 1);
        return false;
      }
      if (event.ctrlKey && key === '-') {
        event.preventDefault();
        setFontSize(currentFontSize - 1);
        return false;
      }
      if (event.ctrlKey && key === '0') {
        event.preventDefault();
        setFontSize(14);
        return false;
      }
      return true;
    });

    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(data);
    });

    termEl.addEventListener('mouseup', () => window.setTimeout(copySelection, 0));
    termEl.addEventListener('touchend', () => window.setTimeout(copySelection, 0), { passive: true });
    termEl.addEventListener('contextmenu', (event) => {
      if (term.hasSelection && term.hasSelection()) return;
      event.preventDefault();
      pasteFromClipboard();
    });

    const onResize = () => {
      fit.fit();
      sendResize();
    };
    window.addEventListener('resize', onResize);

    return {
      close: () => {
        try { ws.close(); } catch (err) {}
        window.removeEventListener('resize', onResize);
        try { term.dispose(); } catch (err) {}
      },
    };
  };

  window.openDevtoolsTerminalPopup = async (resourceId, options = {}) => {
    const popupNamePrefix = String(options.popupNamePrefix || 'terminal_');
    const popupFeatures = String(options.popupFeatures || DEFAULT_FEATURES);
    const wsPath = String(options.wsPath || DEFAULT_WS_PATH);
    const hintText = String(options.hintText || DEFAULT_HINT);
    const title = String(options.title || (resourceId ? (`Resource ${String(resourceId)} terminal`) : 'Terminal'));

    const query = Object.assign({}, options.sessionQuery || {});
    if (resourceId) query.resource_id = String(resourceId);
    const fullWsPath = buildWsPath(wsPath, query);

    const popupName = popupNamePrefix + (resourceId ? String(resourceId) : String(options.popupName || 'session'));
    const popup = window.open('', popupName, popupFeatures);
    if (!popup) return false;

    const html = `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(title)}</title><meta name="viewport" content="width=device-width, initial-scale=1"><style>html,body{height:100%;margin:0;background:#050b12;color:#d7e2f2;font-family:ui-sans-serif,system-ui}.holder{height:100%}</style></head><body><div id="holder" class="holder"></div></body></html>`;
    popup.document.open();
    popup.document.write(html);
    popup.document.close();
    popup.focus();

    const holder = popup.document.getElementById('holder');
    const inject = (tag) => popup.document.head.appendChild(tag);

    const css = popup.document.createElement('link');
    css.rel = 'stylesheet';
    css.href = 'https://cdn.jsdelivr.net/npm/xterm/css/xterm.css';
    inject(css);

    const load = (src) => new Promise((resolve, reject) => {
      const s = popup.document.createElement('script');
      s.src = src;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error(`Failed to load ${src}`));
      inject(s);
    });

    await Promise.all([
      load('https://cdn.jsdelivr.net/npm/xterm/lib/xterm.js'),
      load('https://cdn.jsdelivr.net/npm/xterm-addon-fit/lib/xterm-addon-fit.js'),
      load('https://cdn.jsdelivr.net/npm/xterm-addon-web-links/lib/xterm-addon-web-links.js'),
    ]);

    popup.Terminal = popup.Terminal || popup.window.Terminal;
    popup.FitAddon = popup.FitAddon || popup.window.FitAddon;
    popup.WebLinksAddon = popup.WebLinksAddon || popup.window.WebLinksAddon;

    const client = await (async () => {
      const wrap = popup.document.createElement('div');
      wrap.style.height = '100%';
      holder.appendChild(wrap);

      const term = new popup.Terminal({
        cursorBlink: true,
        scrollback: 50000,
        fontFamily: '"SFMono-Regular","Menlo","Monaco","Consolas","Liberation Mono","Courier New",monospace',
        fontSize: 14,
        theme: { background: '#0a0e12', foreground: '#d7e2f2', cursor: '#6be4a8', selection: 'rgba(107, 228, 168, 0.3)' },
      });
      const fit = new popup.FitAddon.FitAddon();
      term.loadAddon(fit);
      if (popup.WebLinksAddon && popup.WebLinksAddon.WebLinksAddon) {
        term.loadAddon(new popup.WebLinksAddon.WebLinksAddon((event, uri) => {
          if (!shouldActivateTerminalLink(event)) return;
          openTerminalLink(popup, uri);
        }));
      }
      term.open(wrap);
      fit.fit();

      const scheme = popup.location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new popup.WebSocket(scheme + '://' + popup.location.host + fullWsPath);
      ws.binaryType = 'arraybuffer';

      const sendResize = () => {
        const dims = fit.proposeDimensions();
        if (!dims || ws.readyState !== popup.WebSocket.OPEN) return;
        ws.send(JSON.stringify({ type: 'resize', cols: dims.cols, rows: dims.rows }));
      };

      ws.onopen = () => sendResize();
      ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) term.write(new Uint8Array(event.data));
        else term.write(event.data);
      };
      ws.onclose = () => term.write('\r\n[terminal session closed]\r\n');

      term.onData((data) => {
        if (ws.readyState === popup.WebSocket.OPEN) ws.send(data);
      });

      const onResize = () => {
        fit.fit();
        sendResize();
      };
      popup.addEventListener('resize', onResize);

      return {
        close: () => {
          try { ws.close(); } catch (err) {}
          popup.removeEventListener('resize', onResize);
          try { term.dispose(); } catch (err) {}
        },
      };
    })();

    popup.addEventListener('beforeunload', () => client.close());
    return true;
  };

  const removeAskWidget = () => {
    if (askWidgetDragCleanup) {
      askWidgetDragCleanup();
      askWidgetDragCleanup = null;
    }
    if (askClient) {
      askClient.close();
      askClient = null;
    }
    if (askWidget && askWidget.parentNode) {
      askWidget.parentNode.removeChild(askWidget);
    }
    askWidget = null;
    document.body.classList.remove('ask-widget-open');
  };

  const setupDraggableAskWidget = (widget) => {
    if (window.matchMedia('(max-width: 767px)').matches) return () => {};
    const header = widget.querySelector('.ask-terminal-widget__head');
    if (!header) return () => {};

    let dragging = false;
    let startX = 0;
    let startY = 0;
    let startLeft = 0;
    let startTop = 0;

    const clampPosition = (left, top) => {
      const rect = widget.getBoundingClientRect();
      const maxLeft = Math.max(8, window.innerWidth - rect.width - 8);
      const maxTop = Math.max(8, window.innerHeight - rect.height - 8);
      return {
        left: Math.min(Math.max(8, left), maxLeft),
        top: Math.min(Math.max(8, top), maxTop),
      };
    };

    const ensureAbsolutePosition = () => {
      const rect = widget.getBoundingClientRect();
      widget.style.left = `${Math.max(8, rect.left)}px`;
      widget.style.top = `${Math.max(8, rect.top)}px`;
      widget.style.right = 'auto';
      widget.style.bottom = 'auto';
    };

    const onPointerMove = (event) => {
      if (!dragging) return;
      const nextLeft = startLeft + (event.clientX - startX);
      const nextTop = startTop + (event.clientY - startY);
      const clamped = clampPosition(nextLeft, nextTop);
      widget.style.left = `${clamped.left}px`;
      widget.style.top = `${clamped.top}px`;
      widget.style.right = 'auto';
      widget.style.bottom = 'auto';
    };

    const stopDragging = () => {
      if (!dragging) return;
      dragging = false;
      header.classList.remove('is-dragging');
      document.body.classList.remove('ask-terminal-dragging');
      window.removeEventListener('pointermove', onPointerMove);
      window.removeEventListener('pointerup', stopDragging);
      window.removeEventListener('pointercancel', stopDragging);
    };

    const onPointerDown = (event) => {
      if (event.button !== 0) return;
      if (event.target && event.target.closest('.ask-terminal-widget__close')) return;
      if (event.target && event.target.closest('.ask-terminal-widget__sudo')) return;
      event.preventDefault();
      ensureAbsolutePosition();
      const rect = widget.getBoundingClientRect();
      dragging = true;
      startX = event.clientX;
      startY = event.clientY;
      startLeft = rect.left;
      startTop = rect.top;
      header.classList.add('is-dragging');
      document.body.classList.add('ask-terminal-dragging');
      window.addEventListener('pointermove', onPointerMove);
      window.addEventListener('pointerup', stopDragging);
      window.addEventListener('pointercancel', stopDragging);
    };

    const keepInBounds = () => {
      const rect = widget.getBoundingClientRect();
      const clamped = clampPosition(rect.left, rect.top);
      const moved = Math.abs(clamped.left - rect.left) > 0.5 || Math.abs(clamped.top - rect.top) > 0.5;
      if (!moved) return;
      widget.style.left = `${clamped.left}px`;
      widget.style.top = `${clamped.top}px`;
      widget.style.right = 'auto';
      widget.style.bottom = 'auto';
    };

    header.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('resize', keepInBounds);

    return () => {
      stopDragging();
      header.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('resize', keepInBounds);
    };
  };

  const openAskChatWidget = async ({ title }) => {
    removeAskWidget();

    askWidget = document.createElement('section');
    askWidget.className = 'ask-terminal-widget';
    askWidget.innerHTML = `
      <div class="ask-terminal-widget__head">
        <strong>${escapeHtml(title || 'Ask Alshival')}</strong>
        ${isSuperuser ? '<a href="#" class="ask-terminal-widget__sudo" aria-label="Open terminal sudo mode">Sudo mode</a>' : ''}
        <button type="button" class="ask-terminal-widget__close" aria-label="Close chat">×</button>
      </div>
      <div class="ask-terminal-widget__body ask-chat-widget">
        <div class="ask-chat-widget__messages" aria-live="polite">
          <div class="ask-chat-msg ask-chat-msg--assistant">How can I help?</div>
        </div>
        <form class="ask-chat-widget__composer">
          <div class="resource-note-chat">
            <textarea
              rows="1"
              maxlength="8000"
              class="resource-note-input-text"
              data-ask-input
              placeholder="Ask Alshival..."></textarea>
            <div class="resource-note-upload-hints" aria-hidden="true">
              <svg
                class="resource-note-upload-icon"
                xmlns="http://www.w3.org/2000/svg"
                width="24"
                height="24"
                viewBox="0 0 24 24">
                <g fill="none" stroke="currentColor" stroke-width="2">
                  <circle cx="12" cy="13" r="3"></circle>
                  <path d="M9.778 21h4.444c3.121 0 4.682 0 5.803-.735a4.4 4.4 0 0 0 1.226-1.204c.749-1.1.749-2.633.749-5.697s0-4.597-.749-5.697a4.4 4.4 0 0 0-1.226-1.204c-.72-.473-1.622-.642-3.003-.702c-.659 0-1.226-.49-1.355-1.125A2.064 2.064 0 0 0 13.634 3h-3.268c-.988 0-1.839.685-2.033 1.636c-.129.635-.696 1.125-1.355 1.125c-1.38.06-2.282.23-3.003.702A4.4 4.4 0 0 0 2.75 7.667C2 8.767 2 10.299 2 13.364s0 4.596.749 5.697c.324.476.74.885 1.226 1.204C5.096 21 6.657 21 9.778 21Z"></path>
                </g>
              </svg>
              <svg
                class="resource-note-upload-icon"
                xmlns="http://www.w3.org/2000/svg"
                width="24"
                height="24"
                viewBox="0 0 24 24">
                <g fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2">
                  <rect width="18" height="18" x="3" y="3" rx="2" ry="2"></rect>
                  <circle cx="9" cy="9" r="2"></circle>
                  <path d="m21 15l-3.086-3.086a2 2 0 0 0-2.828 0L6 21"></path>
                </g>
              </svg>
              <svg
                class="resource-note-upload-icon"
                xmlns="http://www.w3.org/2000/svg"
                width="24"
                height="24"
                viewBox="0 0 24 24">
                <path
                  fill="none"
                  stroke="currentColor"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  stroke-width="2"
                  d="m6 14l1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"></path>
              </svg>
            </div>
            <button class="resource-note-label-send" type="submit" aria-label="Send message" data-ask-send>
              <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24">
                <path fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="m5 12l7-7l7 7m-7 7V5"></path>
              </svg>
            </button>
          </div>
        </form>
      </div>
    `;
    document.body.appendChild(askWidget);
    document.body.classList.add('ask-widget-open');
    askWidgetDragCleanup = setupDraggableAskWidget(askWidget);

    const closeButton = askWidget.querySelector('.ask-terminal-widget__close');
    closeButton.addEventListener('click', () => removeAskWidget());
    const sudoButton = askWidget.querySelector('.ask-terminal-widget__sudo');
    if (sudoButton) {
      sudoButton.addEventListener('click', async (event) => {
        event.preventDefault();
        await openAskWidget({
          mode: 'shell',
          title: 'System Terminal',
          hintText: 'Superuser host login shell',
        });
      });
    }

    const messagesEl = askWidget.querySelector('.ask-chat-widget__messages');
    const formEl = askWidget.querySelector('.ask-chat-widget__composer');
    const inputEl = askWidget.querySelector('[data-ask-input]');
    const sendEl = askWidget.querySelector('[data-ask-send]');
    let pending = false;

    const addMessage = (role, text) => {
      const node = document.createElement('div');
      node.className = `ask-chat-msg ${role === 'user' ? 'ask-chat-msg--user' : 'ask-chat-msg--assistant'}`;
      node.textContent = String(text || '').trim();
      messagesEl.appendChild(node);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    };

    const setPending = (next) => {
      pending = Boolean(next);
      sendEl.disabled = pending;
      inputEl.disabled = pending;
    };

    inputEl.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      if (event.shiftKey) return;
      if (event.isComposing) return;
      event.preventDefault();
      if (typeof formEl.requestSubmit === 'function') {
        formEl.requestSubmit();
        return;
      }
      formEl.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });

    formEl.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (pending) return;
      const message = String(inputEl.value || '').trim();
      if (!message) return;

      addMessage('user', message);
      inputEl.value = '';
      setPending(true);
      try {
        const response = await fetch(ASK_CHAT_ENDPOINT, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken'),
            'X-Requested-With': 'XMLHttpRequest',
          },
          body: JSON.stringify({ message }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          addMessage('assistant', `Chat unavailable (${String(payload.error || 'request_failed')}).`);
          return;
        }
        addMessage('assistant', String(payload.reply || '').trim() || 'No response.');
      } catch (error) {
        addMessage('assistant', 'Chat unavailable right now.');
      } finally {
        setPending(false);
        inputEl.focus();
      }
    });

    inputEl.focus();
    askClient = { close: () => {} };
  };

  const openAskWidget = async ({ mode, title, hintText }) => {
    removeAskWidget();

    askWidget = document.createElement('section');
    askWidget.className = 'ask-terminal-widget';
    askWidget.innerHTML = `
      <div class="ask-terminal-widget__head">
        <strong>${escapeHtml(title)}</strong>
        <button type="button" class="ask-terminal-widget__close" aria-label="Close terminal">×</button>
      </div>
      <div class="ask-terminal-widget__body"></div>
    `;
    document.body.appendChild(askWidget);
    document.body.classList.add('ask-widget-open');
    askWidgetDragCleanup = setupDraggableAskWidget(askWidget);

    const closeButton = askWidget.querySelector('.ask-terminal-widget__close');
    closeButton.addEventListener('click', () => removeAskWidget());

    const body = askWidget.querySelector('.ask-terminal-widget__body');
    const wsPath = buildWsPath(DEFAULT_WS_PATH, { mode });

    try {
      askClient = await makeTerminalClient({
        container: body,
        title,
        hintText,
        wsPath,
        onSocketClose: ({ opened, durationMs }) => {
          const shouldFallback = mode === 'shell' && (!opened || durationMs < 2000);
          if (!shouldFallback || !askWidget || !askWidget.isConnected) return;
          window.setTimeout(() => {
            if (!askWidget || !askWidget.isConnected) return;
            openAskChatWidget({ title: 'Ask Alshival' });
          }, 200);
        },
      });
    } catch (error) {
      if (mode === 'shell') {
        await openAskChatWidget({ title: 'Ask Alshival' });
        return;
      }
      body.innerHTML = '<div class="ask-terminal-widget__error">Failed to start terminal.</div>';
    }
  };

  window.openAskAlshivalWidget = async (options = {}) => {
    const mode = String((options && options.mode) || 'chat').trim().toLowerCase();
    const title = String((options && options.title) || 'Ask Alshival');
    const hintText = String((options && options.hintText) || DEFAULT_HINT);
    if (mode === 'shell') {
      if (!isSuperuser) return false;
      await openAskWidget({
        mode: 'shell',
        title,
        hintText,
      });
      return true;
    }
    await openAskChatWidget({ title });
    return true;
  };

  const askButton = document.querySelector('.floating-ask-alshival');
  if (askButton) {
    askButton.addEventListener('click', async (event) => {
      event.preventDefault();
      await window.openAskAlshivalWidget({ mode: 'chat', title: 'Ask Alshival' });
    });
  }
})();
