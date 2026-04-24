(function () {
  const pageMatrixCanvas = document.getElementById('landing-page-matrix');

  const initMatrixCanvas = function (canvas) {
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
    let currentDpr = window.devicePixelRatio || 1;
    let lastSizeCheck = 0;
    let resizeObserver = null;
    const frameDelay = 50;
    const maxCanvasPixels = 16777216;
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

    const setCanvasSize = function () {
      const rect = canvas.getBoundingClientRect();
      const nextWidth = Math.max(1, Math.round(rect.width || 0));
      const nextHeight = Math.max(1, Math.round(rect.height || 0));
      const widthChanged = nextWidth !== lastWidth;
      const heightChanged = nextHeight !== lastHeight;
      const maxDpr = Math.sqrt(maxCanvasPixels / (nextWidth * nextHeight));
      const dpr = window.devicePixelRatio || 1;
      const effectiveDpr = Math.max(1, Math.min(dpr, maxDpr));
      const dprChanged = Math.abs(effectiveDpr - currentDpr) > 0.001;

      if (!widthChanged && !heightChanged && !dprChanged) {
        return;
      }

      currentDpr = effectiveDpr;
      lastWidth = nextWidth;
      lastHeight = nextHeight;
      canvas.style.width = nextWidth + 'px';
      canvas.style.height = nextHeight + 'px';
      canvas.width = Math.max(1, Math.round(nextWidth * effectiveDpr));
      canvas.height = Math.max(1, Math.round(nextHeight * effectiveDpr));
      ctx.setTransform(effectiveDpr, 0, 0, effectiveDpr, 0, 0);

      if (widthChanged || heightChanged) {
        columns = Math.max(1, Math.ceil(nextWidth / fontSize));
        drops = new Array(columns).fill(0).map(function () {
          return Math.random() * (nextHeight / fontSize);
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
      ctx.fillRect(0, 0, lastWidth, lastHeight);
      ctx.font = fontSize + 'px monospace';

      for (let i = 0; i < drops.length; i += 1) {
        const text = characters.charAt(Math.floor(Math.random() * characters.length));
        const x = i * fontSize;
        const y = drops[i] * fontSize;
        ctx.fillStyle = i % 3 === 0 ? colorA : colorB;
        ctx.fillText(text, x, y);
        if (y > lastHeight && Math.random() > 0.95) {
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
    initMatrixCanvas(pageMatrixCanvas);
  }
})();
