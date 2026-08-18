(() => {
  if (typeof history !== "undefined" && "scrollRestoration" in history) {
    history.scrollRestoration = "manual";
  }
  const cacheKey = "loreloop:reader-preferences";
  const legacyCacheKey = "tvg:reader-preferences";
  try {
    let serialized = localStorage.getItem(cacheKey);
    if (serialized === null) {
      const legacy = localStorage.getItem(legacyCacheKey);
      if (legacy !== null) {
        localStorage.setItem(cacheKey, legacy);
        localStorage.removeItem(legacyCacheKey);
        serialized = legacy;
      }
    } else {
      localStorage.removeItem(legacyCacheKey);
    }
    const preferences = JSON.parse(serialized || "null");
    if (!preferences) return;
    const themes = new Set(["dark", "sepia", "light"]);
    const fonts = new Set(["serif", "kaiti", "ming", "sans"]);
    if (themes.has(preferences.theme) && preferences.theme !== "dark") {
      document.documentElement.dataset.theme = preferences.theme;
    }
    if (
      fonts.has(preferences.font_family)
      && preferences.font_family !== "serif"
    ) {
      document.documentElement.dataset.fontFamily = preferences.font_family;
    }
    if (/^(14|15|16\.5|18|20)$/.test(String(preferences.font_size || ""))) {
      document.documentElement.style.setProperty(
        "--narrative-font-size",
        `${preferences.font_size}px`,
      );
    }
    if (/^#[0-9a-f]{6}$/i.test(String(preferences.theme_color || ""))) {
      document.querySelector('meta[name="theme-color"]').content = (
        preferences.theme_color
      );
    }
  } catch (_error) {
    localStorage.removeItem(cacheKey);
  }
})();
