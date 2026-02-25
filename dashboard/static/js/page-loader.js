(function () {
  const introStorageKey = "alshival_intro_seen";
  const root = document.documentElement;
  const shouldRunIntro = root.classList.contains("show-session-intro");
  if (!shouldRunIntro) {
    root.classList.remove("page-loading", "show-session-intro");
    root.classList.add("page-ready", "ui-visible");
    return;
  }

  const startedAt = Date.now();
  const minVisibleMs = 900;
  let finished = false;
  let typingDone = false;
  let pageLoaded = document.readyState === "complete";
  let minElapsed = false;

  const typewriterTarget = document.querySelector("[data-typewriter-target]");
  const typewriterText = typewriterTarget ? (typewriterTarget.getAttribute("data-typewriter-target") || "Alshival") : "";

  const markIntroSeen = () => {
    try {
      window.sessionStorage.setItem(introStorageKey, "1");
    } catch (error) {
      // Ignore storage failures; intro will re-run when storage is blocked.
    }
  };

  const maybeFinish = () => {
    if (finished || !typingDone || !pageLoaded || !minElapsed) {
      return;
    }
    finished = true;
    markIntroSeen();
    root.classList.add("page-ready");
    window.setTimeout(() => {
      root.classList.remove("page-loading", "show-session-intro");
      window.requestAnimationFrame(() => {
        root.classList.add("ui-visible");
      });
    }, 140);
  };

  const runTypewriter = () => {
    if (!typewriterTarget || !typewriterText) {
      typingDone = true;
      maybeFinish();
      return;
    }
    const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) {
      typewriterTarget.textContent = typewriterText;
      typingDone = true;
      maybeFinish();
      return;
    }
    let index = 0;
    typewriterTarget.textContent = "";
    const timer = window.setInterval(() => {
      index += 1;
      typewriterTarget.textContent = typewriterText.slice(0, index);
      if (index >= typewriterText.length) {
        window.clearInterval(timer);
        typingDone = true;
        maybeFinish();
      }
    }, 55);
  };

  const finish = () => {
    pageLoaded = true;
    maybeFinish();
  };

  runTypewriter();

  const elapsed = Date.now() - startedAt;
  const remaining = Math.max(0, minVisibleMs - elapsed);
  window.setTimeout(() => {
    minElapsed = true;
    maybeFinish();
  }, remaining);

  if (document.readyState === "complete") {
    window.setTimeout(finish, 0);
  } else {
    window.addEventListener("load", finish, { once: true });
  }
  window.addEventListener("pageshow", finish, { once: true });
})();
