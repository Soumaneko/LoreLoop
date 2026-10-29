export function createAppNavigation({ saveId, beforeLeave, onChange }) {
  const reading = document.getElementById("readingPage");
  const library = document.getElementById("journeyLibraryPage");
  const readingOnly = [...document.querySelectorAll("[data-reading-only]")];
  let current = "library";
  let revision = 0;
  let navigating = false;
  let readingPosition = null;
  const positions = new Map();
  let modelSource = "library";
  document.body.dataset.page = current;

  function display() {
    const isReading = current === "reading";
    for (const element of readingOnly) { element.hidden = !isReading; element.inert = !isReading; }
    for (const element of document.querySelectorAll("[data-global-only]")) { element.hidden = isReading; element.inert = isReading; }
    library.hidden = current !== "library"; library.inert = library.hidden;
    const models = current === "models" || current === "modelEditor";
    const modelPage = document.getElementById("modelPage");
    modelPage.hidden = !models; modelPage.inert = !models;
    document.getElementById("modelConfigsDialog").hidden = current !== "models";
    document.getElementById("modelConfigDialog").hidden = current !== "modelEditor";
    document.getElementById("addModelServiceBtn").hidden = current !== "models";
    document.getElementById("modelPageTitle").textContent = current === "modelEditor" ? "配置模型服务" : "模型服务";
    document.getElementById("modelBackBtn").textContent = current === "modelEditor" ? "‹ 返回模型服务" : modelSource === "reading" ? "‹ 返回故事" : "‹ 返回旅程库";
    for (const [id, active] of [["libraryNavBtn", current === "library"], ["libraryModelsBtn", models]]) {
      const button = document.getElementById(id);
      if (active) button.setAttribute("aria-current", "page"); else button.removeAttribute("aria-current");
    }
    document.body.dataset.page = current;
  }
  display();

  function captureReading() {
    const anchor = [...reading.querySelectorAll(".turn[data-turn-index], #activePlayerAction, #pendingCard")]
      .find(element => element.getClientRects().length && element.getBoundingClientRect().bottom > 90);
    return {
      saveId: saveId(), y: window.scrollY, focus: document.activeElement,
      selector: anchor?.id ? `#${anchor.id}`
        : anchor ? `.turn[data-turn-index="${anchor.dataset.turnIndex}"]` : null,
      top: anchor?.getBoundingClientRect().top,
    };
  }

  async function show(page) {
    if (navigating || current === page) return false;
    navigating = true;
    try {
      if (!await beforeLeave(page)) return false;
      const previous = current;
      if (!["models", "modelEditor"].includes(current) && ["models", "modelEditor"].includes(page)) modelSource = current;
      if (current === "reading") readingPosition = captureReading();
      else if (current !== "modelEditor") positions.set(current, { y: window.scrollY, focus: document.activeElement });
      // Cancel an in-progress smooth scroll before replacing the visible surface.
      window.scrollTo({ top: window.scrollY, behavior: "instant" });
      current = page;
      revision += 1;
      const visit = revision;
      const isReading = current === "reading";
      display();
      onChange(previous);
      const restore = (restoreFocus = true) => {
        if (visit !== revision) return;
        const position = isReading && readingPosition?.saveId === saveId() ? readingPosition : null;
        const anchor = position?.selector && reading.querySelector(position.selector);
        const y = isReading ? (anchor ? window.scrollY + anchor.getBoundingClientRect().top - position.top : position?.y || 0) : positions.get(current)?.y || 0;
        window.scrollTo({ top: y, behavior: "instant" });
        if (!restoreFocus) return;
        const focus = position?.focus || (!isReading && positions.get(current)?.focus);
        if (focus?.isConnected && !focus.disabled && !focus.closest("[hidden], [inert]")) focus.focus({ preventScroll: true });
        else document.getElementById(isReading ? "openJourneyLibraryBtn" : current === "library" ? "journeyLibraryTitle" : "modelPageTitle").focus({ preventScroll: true });
      };
      restore();
      window.requestAnimationFrame(() => restore(false));
      return true;
    } finally {
      navigating = false;
    }
  }

  return {
    isReading: () => current === "reading",
    isLibrary: () => current === "library",
    isModels: () => current === "models" || current === "modelEditor",
    isModelEditor: () => current === "modelEditor",
    show,
    backModels: () => show(current === "modelEditor" ? "models" : modelSource),
    get revision() { return revision; },
    showLibrary: () => show("library"),
    showReading: () => show("reading"),
    invalidateReading: () => { readingPosition = null; },
  };
}
