import { syncCustomSelect } from "./custom_select.js";

const CACHE_KEY = "loreloop:reader-preferences";
const THEME_COLORS = {
  dark: "#11110e",
  sepia: "#e6ddcc",
  light: "#dce0e6",
};

const DEFAULTS = {
  font_size: "16.5",
  font_family: "serif",
  theme: "dark",
  show_token_usage: false,
};

function normalized(preferences = {}) {
  return {
    font_size: preferences.font_size || DEFAULTS.font_size,
    font_family: preferences.font_family || DEFAULTS.font_family,
    theme: preferences.theme || DEFAULTS.theme,
    show_token_usage: preferences.show_token_usage === true,
  };
}

export function createReaderPreferencesController({
  elements,
  request,
  state,
  showToast,
  showError,
  onTokenUsageVisibilityChange,
}) {
  let saveQueue = Promise.resolve();
  let saveSequence = 0;
  let confirmed = normalized(state.readerPreferences);

  const apply = (preferences = DEFAULTS) => {
    const current = normalized(preferences);
    const root = document.documentElement;
    root.style.setProperty("--narrative-font-size", `${current.font_size}px`);
    if (current.font_family === "serif") root.removeAttribute("data-font-family");
    else root.dataset.fontFamily = current.font_family;
    if (current.theme === "dark") root.removeAttribute("data-theme");
    else root.dataset.theme = current.theme;

    const themeColor = THEME_COLORS[current.theme] || THEME_COLORS.dark;
    const themeColorMeta = document.querySelector('meta[name="theme-color"]');
    if (themeColorMeta) themeColorMeta.content = themeColor;
    try {
      localStorage.setItem(CACHE_KEY, JSON.stringify({
        ...current,
        theme_color: themeColor,
      }));
    } catch (_error) {
      // The durable copy lives in the application-level settings file.
    }

    elements.fontSize.value = current.font_size;
    elements.fontFamily.value = current.font_family;
    elements.theme.value = current.theme;
    syncCustomSelect(elements.fontSize);
    syncCustomSelect(elements.fontFamily);
    syncCustomSelect(elements.theme);

    if (elements.tokenUsage) {
      elements.tokenUsage.value = current.show_token_usage ? "shown" : "hidden";
      syncCustomSelect(elements.tokenUsage);
    }
    if (onTokenUsageVisibilityChange) {
      onTokenUsageVisibilityChange(current.show_token_usage);
    }
  };

  const load = async () => {
    const preferences = await request("/api/player/reader-preferences");
    confirmed = normalized(preferences);
    state.readerPreferences = confirmed;
    apply(state.readerPreferences);
    return state.readerPreferences;
  };

  const save = async () => {
    const requested = {
      font_size: elements.fontSize.value || DEFAULTS.font_size,
      font_family: elements.fontFamily.value || DEFAULTS.font_family,
      theme: elements.theme.value || DEFAULTS.theme,
      show_token_usage: elements.tokenUsage ? elements.tokenUsage.value === "shown" : false,
    };
    apply(requested);
    const sequence = ++saveSequence;
    const job = saveQueue
      .catch(() => undefined)
      .then(() => request("/api/player/reader-preferences", {
        method: "POST",
        body: JSON.stringify(requested),
      }));
    saveQueue = job;
    try {
      const saved = normalized(await job);
      confirmed = saved;
      state.readerPreferences = saved;
      if (sequence === saveSequence) {
        apply(saved);
        showToast("沉浸阅读偏好已保存");
      }
    } catch (error) {
      if (sequence === saveSequence) {
        state.readerPreferences = confirmed;
        apply(confirmed);
        showError(error);
      }
    }
  };

  return { apply, load, save };
}
