(function () {
  const pageMatrixCanvas = document.getElementById('landing-page-matrix');

  const initMatrixCanvas = function (canvas, sizeMode) {
    const prefersReducedMotion = window.matchMedia
      ? window.matchMedia('(prefers-reduced-motion: reduce)').matches
      : false;
    if (prefersReducedMotion) {
      return;
    }

    const ctx = canvas.getContext('2d');
    if (!ctx) {
      return;
    }

    const characters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&*+-=∑ΣπθλΩαβγΔλμ∀∂∫√∞≈→←あカサタナハマヤラワンアイウエオ한글테스트';
    const fontSize = 16;
    let columns = 0;
    let drops = [];
    let animationFrame = null;
    let resizeTimeout = null;
    let lastFrame = 0;
    let lastWidth = 0;
    let lastHeight = 0;
    let renderWidth = 0;
    let renderHeight = 0;
    let currentDpr = window.devicePixelRatio || 1;
    let lastSizeCheck = 0;
    let resizeObserver = null;
    const frameDelay = 50;
    let colorA = '#5eead4';
    let colorB = '#38bdf8';
    let fadeColor = 'rgba(7, 10, 20, 0.15)';

    const applyThemeColors = function () {
      const styles = window.getComputedStyle(canvas);
      const nextColorA = styles.getPropertyValue('--matrix-color-a').trim();
      const nextColorB = styles.getPropertyValue('--matrix-color-b').trim();
      const nextFade = styles.getPropertyValue('--matrix-fade').trim();
      if (nextColorA) colorA = nextColorA;
      if (nextColorB) colorB = nextColorB;
      if (nextFade) fadeColor = nextFade;
    };

    const getCanvasRect = function () {
      if (sizeMode === 'viewport') {
        const visualViewport = window.visualViewport;
        if (visualViewport && visualViewport.width && visualViewport.height) {
          return {
            width: visualViewport.width,
            height: visualViewport.height
          };
        }
        return {
          width: window.innerWidth,
          height: window.innerHeight
        };
      }
      return canvas.getBoundingClientRect();
    };

    const setCanvasSize = function () {
      const dpr = window.devicePixelRatio || 1;
      const rect = getCanvasRect();
      const rectWidth = Math.max(1, rect.width || 0);
      const rectHeight = Math.max(1, rect.height || 0);
      const widthChanged = !lastWidth || Math.round(rect.width) !== Math.round(lastWidth);
      const heightChanged = !lastHeight || Math.round(rect.height) !== Math.round(lastHeight);
      const dprChanged = Math.abs(dpr - currentDpr) > 0.001;
      if (!widthChanged && !heightChanged && !dprChanged) {
        return;
      }

      currentDpr = dpr;
      lastWidth = rectWidth;
      lastHeight = rectHeight;
      renderWidth = rectWidth;
      renderHeight = rectHeight;
      canvas.width = Math.max(1, Math.round(rectWidth * dpr));
      canvas.height = Math.max(1, Math.round(rectHeight * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      if (widthChanged || heightChanged) {
        columns = Math.max(1, Math.floor(rectWidth / fontSize));
        const maxDropStart = Math.max(1, Math.ceil(rectHeight / fontSize));
        drops = new Array(columns).fill(0).map(function () {
          return Math.random() * maxDropStart;
        });
      }
    };

    const draw = function (timestamp) {
      if (timestamp - lastSizeCheck > 250) {
        setCanvasSize();
        lastSizeCheck = timestamp;
      }
      if (timestamp - lastFrame < frameDelay) {
        animationFrame = window.requestAnimationFrame(draw);
        return;
      }
      lastFrame = timestamp;

      ctx.fillStyle = fadeColor;
      ctx.fillRect(0, 0, renderWidth, renderHeight);
      ctx.font = fontSize + 'px monospace';

      for (let i = 0; i < drops.length; i += 1) {
        const text = characters.charAt(Math.floor(Math.random() * characters.length));
        const x = i * fontSize;
        const y = drops[i] * fontSize;
        ctx.fillStyle = i % 3 === 0 ? colorA : colorB;
        ctx.fillText(text, x, y);
        if (y > renderHeight && Math.random() > 0.95) {
          drops[i] = 0;
        } else {
          drops[i] += 1;
        }
      }

      animationFrame = window.requestAnimationFrame(draw);
    };

    const handleResize = function () {
      window.clearTimeout(resizeTimeout);
      resizeTimeout = window.setTimeout(setCanvasSize, 120);
    };

    applyThemeColors();
    setCanvasSize();
    window.addEventListener('resize', handleResize);
    if (window.visualViewport) {
      window.visualViewport.addEventListener('resize', handleResize);
    }
    if (window.ResizeObserver) {
      resizeObserver = new ResizeObserver(handleResize);
      resizeObserver.observe(canvas);
    }
    animationFrame = window.requestAnimationFrame(draw);

    const themeObserver = new MutationObserver(function (mutations) {
      if (mutations.some(function (mutation) {
        return mutation.attributeName === 'class';
      })) {
        applyThemeColors();
      }
    });

    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class']
    });

    window.addEventListener('beforeunload', function () {
      if (animationFrame) {
        window.cancelAnimationFrame(animationFrame);
      }
      window.removeEventListener('resize', handleResize);
      if (window.visualViewport) {
        window.visualViewport.removeEventListener('resize', handleResize);
      }
      if (resizeObserver) {
        resizeObserver.disconnect();
        resizeObserver = null;
      }
      themeObserver.disconnect();
    });
  };

  if (pageMatrixCanvas) {
    initMatrixCanvas(pageMatrixCanvas, 'element');
  }
})();
