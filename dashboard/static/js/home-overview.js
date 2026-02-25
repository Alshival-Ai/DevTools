(() => {
  const dashboard = document.querySelector('[data-overview-dashboard]');
  if (!dashboard) {
    return;
  }

  const healthNode = document.getElementById('overview-health-timeline');
  const logNode = document.getElementById('overview-log-timeline');
  const healthCanvas = dashboard.querySelector('[data-health-chart]');
  const logCanvas = dashboard.querySelector('[data-log-chart]');

  const parseJsonNode = (node) => {
    if (!node) return [];
    try {
      const parsed = JSON.parse(node.textContent || '[]');
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      return [];
    }
  };

  const healthSeries = parseJsonNode(healthNode);
  const logSeries = parseJsonNode(logNode);

  const getCookie = (name) => {
    const cookieString = document.cookie || '';
    if (!cookieString) {
      return '';
    }
    const parts = cookieString.split(';');
    for (let i = 0; i < parts.length; i += 1) {
      const part = parts[i].trim();
      if (part.startsWith(`${name}=`)) {
        return decodeURIComponent(part.slice(name.length + 1));
      }
    }
    return '';
  };

  const toNumber = (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0) {
      return 0;
    }
    return parsed;
  };

  const createCanvasContext = (canvas, height) => {
    if (!canvas) return null;
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(320, canvas.clientWidth || 0);
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);
    return { ctx, width, height };
  };

  const drawGrid = ({ ctx, width, height, pad, maxValue }) => {
    const rows = 4;
    ctx.strokeStyle = 'rgba(148, 163, 184, 0.18)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= rows; i += 1) {
      const y = pad.top + (i / rows) * (height - pad.top - pad.bottom);
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(width - pad.right, y);
      ctx.stroke();
    }

    ctx.fillStyle = 'rgba(148, 163, 184, 0.85)';
    ctx.font = '11px "IBM Plex Sans", system-ui, sans-serif';
    ctx.fillText(String(Math.max(0, Math.round(maxValue))), pad.left, pad.top - 5);
    ctx.fillText('0', pad.left, height - pad.bottom + 14);
  };

  const drawLineChart = (canvas, series, config) => {
    const setup = createCanvasContext(canvas, 220);
    if (!setup) return;
    const { ctx, width, height } = setup;
    const keys = config.keys || [];
    const pad = { top: 20, right: 12, bottom: 28, left: 36 };
    const chartWidth = width - pad.left - pad.right;
    const chartHeight = height - pad.top - pad.bottom;
    if (!series.length || chartWidth <= 0 || chartHeight <= 0) {
      ctx.fillStyle = 'rgba(148, 163, 184, 0.8)';
      ctx.font = '13px "IBM Plex Sans", system-ui, sans-serif';
      ctx.fillText('No data yet', pad.left, height / 2);
      return;
    }

    let maxValue = 0;
    series.forEach((item) => {
      keys.forEach((key) => {
        maxValue = Math.max(maxValue, toNumber(item[key]));
      });
    });
    maxValue = Math.max(4, Math.ceil(maxValue));

    drawGrid({ ctx, width, height, pad, maxValue });

    const pointX = (index) => {
      if (series.length <= 1) {
        return pad.left + chartWidth / 2;
      }
      return pad.left + (index / (series.length - 1)) * chartWidth;
    };

    keys.forEach((key) => {
      const color = config.palette[key] || '#94a3b8';
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      series.forEach((item, index) => {
        const x = pointX(index);
        const value = toNumber(item[key]);
        const y = pad.top + (1 - (value / maxValue)) * chartHeight;
        if (index === 0) {
          ctx.moveTo(x, y);
        } else {
          ctx.lineTo(x, y);
        }
      });
      ctx.stroke();

      ctx.fillStyle = color;
      series.forEach((item, index) => {
        const x = pointX(index);
        const value = toNumber(item[key]);
        const y = pad.top + (1 - (value / maxValue)) * chartHeight;
        ctx.beginPath();
        ctx.arc(x, y, 2.8, 0, Math.PI * 2);
        ctx.fill();
      });
    });

    const firstLabel = String(series[0].label || '');
    const midLabel = String(series[Math.floor(series.length / 2)].label || '');
    const lastLabel = String(series[series.length - 1].label || '');
    ctx.fillStyle = 'rgba(148, 163, 184, 0.85)';
    ctx.font = '11px "IBM Plex Sans", system-ui, sans-serif';
    ctx.fillText(firstLabel, pad.left, height - 8);
    const midWidth = ctx.measureText(midLabel).width;
    ctx.fillText(midLabel, pad.left + chartWidth / 2 - midWidth / 2, height - 8);
    const lastWidth = ctx.measureText(lastLabel).width;
    ctx.fillText(lastLabel, width - pad.right - lastWidth, height - 8);
  };

  const drawStackedBars = (canvas, series, config) => {
    const setup = createCanvasContext(canvas, 220);
    if (!setup) return;
    const { ctx, width, height } = setup;
    const keys = config.keys || [];
    const pad = { top: 20, right: 12, bottom: 28, left: 36 };
    const chartWidth = width - pad.left - pad.right;
    const chartHeight = height - pad.top - pad.bottom;
    if (!series.length || chartWidth <= 0 || chartHeight <= 0) {
      ctx.fillStyle = 'rgba(148, 163, 184, 0.8)';
      ctx.font = '13px "IBM Plex Sans", system-ui, sans-serif';
      ctx.fillText('No data yet', pad.left, height / 2);
      return;
    }

    let maxStack = 0;
    series.forEach((item) => {
      const total = keys.reduce((sum, key) => sum + toNumber(item[key]), 0);
      maxStack = Math.max(maxStack, total);
    });
    maxStack = Math.max(4, Math.ceil(maxStack));

    drawGrid({ ctx, width, height, pad, maxValue: maxStack });

    const band = chartWidth / series.length;
    const barWidth = Math.max(8, Math.min(22, band * 0.62));
    series.forEach((item, index) => {
      const x = pad.left + index * band + (band - barWidth) / 2;
      let runningHeight = 0;
      keys.forEach((key) => {
        const value = toNumber(item[key]);
        if (value <= 0) return;
        const segmentHeight = (value / maxStack) * chartHeight;
        const y = pad.top + chartHeight - runningHeight - segmentHeight;
        ctx.fillStyle = config.palette[key] || '#94a3b8';
        ctx.fillRect(x, y, barWidth, segmentHeight);
        runningHeight += segmentHeight;
      });
    });

    const firstLabel = String(series[0].label || '');
    const midLabel = String(series[Math.floor(series.length / 2)].label || '');
    const lastLabel = String(series[series.length - 1].label || '');
    ctx.fillStyle = 'rgba(148, 163, 184, 0.85)';
    ctx.font = '11px "IBM Plex Sans", system-ui, sans-serif';
    ctx.fillText(firstLabel, pad.left, height - 8);
    const midWidth = ctx.measureText(midLabel).width;
    ctx.fillText(midLabel, pad.left + chartWidth / 2 - midWidth / 2, height - 8);
    const lastWidth = ctx.measureText(lastLabel).width;
    ctx.fillText(lastLabel, width - pad.right - lastWidth, height - 8);
  };

  const redrawCharts = () => {
    drawLineChart(healthCanvas, healthSeries, {
      keys: ['healthy', 'unhealthy', 'unknown'],
      palette: {
        healthy: '#22c55e',
        unhealthy: '#ef4444',
        unknown: '#94a3b8',
      },
    });

    drawStackedBars(logCanvas, logSeries, {
      keys: ['error', 'warning', 'info'],
      palette: {
        error: '#f97316',
        warning: '#facc15',
        info: '#60a5fa',
      },
    });
  };

  let resizeTimer = null;
  window.addEventListener('resize', () => {
    if (resizeTimer) {
      window.clearTimeout(resizeTimer);
    }
    resizeTimer = window.setTimeout(redrawCharts, 120);
  });
  redrawCharts();

  const askMessages = dashboard.querySelector('[data-ask-messages]');
  const askForm = dashboard.querySelector('[data-ask-form]');
  const askInput = dashboard.querySelector('[data-ask-input]');
  const askSend = dashboard.querySelector('[data-ask-send]');
  const askSudo = dashboard.querySelector('[data-ask-sudo]');
  const chatUrl = dashboard.getAttribute('data-chat-url') || '';

  let askPending = false;
  let conversationId = '';
  try {
    const saved = window.localStorage.getItem('overview_ask_conversation_id') || '';
    conversationId = saved || `overview-${Date.now()}`;
  } catch (error) {
    conversationId = `overview-${Date.now()}`;
  }

  const addAskMessage = (role, text) => {
    if (!askMessages) return;
    const node = document.createElement('div');
    node.className = `ask-chat-msg ${role === 'user' ? 'ask-chat-msg--user' : 'ask-chat-msg--assistant'}`;
    node.textContent = String(text || '').trim();
    askMessages.appendChild(node);
    askMessages.scrollTop = askMessages.scrollHeight;
  };

  const setAskPending = (next) => {
    askPending = Boolean(next);
    if (askSend) askSend.disabled = askPending;
    if (askInput) askInput.disabled = askPending;
  };

  if (askInput && askForm) {
    askInput.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' || event.shiftKey || event.isComposing) return;
      event.preventDefault();
      if (typeof askForm.requestSubmit === 'function') {
        askForm.requestSubmit();
      } else {
        askForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
      }
    });
  }

  if (askForm && askInput && chatUrl) {
    askForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (askPending) return;
      const message = String(askInput.value || '').trim();
      if (!message) return;

      addAskMessage('user', message);
      askInput.value = '';
      setAskPending(true);

      try {
        const response = await fetch(chatUrl, {
          method: 'POST',
          credentials: 'same-origin',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCookie('csrftoken'),
            'X-Requested-With': 'XMLHttpRequest',
          },
          body: JSON.stringify({
            message,
            conversation_id: conversationId,
          }),
        });

        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          addAskMessage('assistant', `Chat unavailable (${String(payload.error || 'request_failed')}).`);
          return;
        }

        const nextConversationId = String(payload.conversation_id || '').trim();
        if (nextConversationId) {
          conversationId = nextConversationId;
          try {
            window.localStorage.setItem('overview_ask_conversation_id', conversationId);
          } catch (error) {
            // Ignore storage write issues.
          }
        }
        addAskMessage('assistant', String(payload.reply || '').trim() || 'No response.');
      } catch (error) {
        addAskMessage('assistant', 'Chat unavailable right now.');
      } finally {
        setAskPending(false);
        askInput.focus();
      }
    });
  }

  if (askSudo) {
    askSudo.addEventListener('click', async (event) => {
      event.preventDefault();
      const isSuperuser = dashboard.getAttribute('data-superuser') === '1';
      if (!isSuperuser) {
        return;
      }

      if (typeof window.openAskAlshivalWidget === 'function') {
        await window.openAskAlshivalWidget({
          mode: 'shell',
          title: 'System Terminal',
          hintText: 'Superuser host login shell',
        });
        return;
      }

      addAskMessage('assistant', 'Sudo mode terminal is unavailable on this page.');
    });
  }

  const notifyList = dashboard.querySelector('[data-notify-list]');
  const notifyUnread = dashboard.querySelector('[data-notify-unread]');
  const notifyMarkRead = dashboard.querySelector('[data-notify-mark-read]');
  const notifyClear = dashboard.querySelector('[data-notify-clear]');
  const notificationsUrl = dashboard.getAttribute('data-notifications-url') || '';
  const notificationsMarkReadUrl = dashboard.getAttribute('data-notifications-mark-read-url') || '';
  const notificationsClearUrl = dashboard.getAttribute('data-notifications-clear-url') || '';

  const clearNotifyList = () => {
    if (!notifyList) return;
    while (notifyList.firstChild) {
      notifyList.removeChild(notifyList.firstChild);
    }
  };

  const renderNotifyEmpty = (message) => {
    if (!notifyList) return;
    clearNotifyList();
    const empty = document.createElement('p');
    empty.className = 'notification-empty';
    empty.textContent = message;
    notifyList.appendChild(empty);
  };

  const formatWhen = (value) => {
    if (!value) return '';
    const normalized = value.includes('T') ? value : value.replace(' ', 'T');
    const parsed = new Date(normalized.endsWith('Z') ? normalized : `${normalized}Z`);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    try {
      return new Intl.DateTimeFormat([], {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      }).format(parsed);
    } catch (error) {
      return parsed.toLocaleString();
    }
  };

  const setUnread = (count) => {
    if (!notifyUnread) return;
    const value = Number.isFinite(Number(count)) ? Math.max(0, Number(count)) : 0;
    notifyUnread.textContent = `${value} unread`;
  };

  const renderNotifications = (items) => {
    if (!notifyList) return;
    clearNotifyList();
    if (!Array.isArray(items) || items.length === 0) {
      renderNotifyEmpty('No alerts yet.');
      return;
    }

    items.forEach((item) => {
      const level = String(item && item.level ? item.level : 'info').toLowerCase();
      const title = String(item && item.title ? item.title : 'Notification');
      const body = String(item && item.body ? item.body : '');
      const createdAt = String(item && item.created_at ? item.created_at : '');

      const card = document.createElement('article');
      card.className = `notification-item notification-item--${level}`;

      const head = document.createElement('header');
      head.className = 'notification-item__head';

      const titleNode = document.createElement('h4');
      titleNode.className = 'notification-item__title';
      titleNode.textContent = title;

      const timeNode = document.createElement('time');
      timeNode.className = 'notification-item__time';
      timeNode.textContent = formatWhen(createdAt);

      head.appendChild(titleNode);
      head.appendChild(timeNode);

      const bodyNode = document.createElement('p');
      bodyNode.className = 'notification-item__body';
      bodyNode.textContent = body;

      card.appendChild(head);
      card.appendChild(bodyNode);
      notifyList.appendChild(card);
    });
  };

  const loadNotifications = async () => {
    if (!notificationsUrl) return;
    try {
      const response = await fetch(`${notificationsUrl}?limit=8`, {
        method: 'GET',
        credentials: 'same-origin',
      });
      if (!response.ok) {
        throw new Error(`notifications_fetch_${response.status}`);
      }
      const payload = await response.json();
      const unread = Number(payload && payload.unread_count ? payload.unread_count : 0);
      const items = payload && Array.isArray(payload.items) ? payload.items : [];
      setUnread(unread);
      renderNotifications(items);
    } catch (error) {
      renderNotifyEmpty('Unable to load notifications.');
    }
  };

  const markAllNotificationsRead = async () => {
    if (!notificationsMarkReadUrl) return;
    try {
      const response = await fetch(notificationsMarkReadUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': getCookie('csrftoken'),
        },
      });
      if (!response.ok) return;
      await loadNotifications();
    } catch (error) {
      // Keep controls interactive when request fails.
    }
  };

  const clearAllNotifications = async () => {
    if (!notificationsClearUrl) return;
    try {
      const response = await fetch(notificationsClearUrl, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'X-CSRFToken': getCookie('csrftoken'),
        },
      });
      if (!response.ok) return;
      setUnread(0);
      renderNotifyEmpty('No alerts yet.');
    } catch (error) {
      // Keep controls interactive when request fails.
    }
  };

  if (notifyMarkRead) {
    notifyMarkRead.addEventListener('click', async () => {
      await markAllNotificationsRead();
    });
  }

  if (notifyClear) {
    notifyClear.addEventListener('click', async () => {
      await clearAllNotifications();
    });
  }

  loadNotifications();
})();
