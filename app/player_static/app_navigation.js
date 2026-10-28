export function createAppNavigation({ saveId, beforeLeave, onChange }) {
  const reading = document.getElementById("readingPage");
  const library = document.getElementById("journeyLibraryPage");
  const readingOnly = [...document.querySelectorAll("[data-reading-only]")];
  let current = "library";
  let revision = 0;
  let navigating = false;
  let readingPosition = null;
  let libraryY = 0;
  document.body.dataset.page = current;

  function display() {
    const isReading = current === "reading";
    for (const element of readingOnly) { element.hidden = !isReading; element.inert = !isReading; }
    for (const element of document.querySelectorAll("[data-library-only]")) { element.hidden = isReading; element.inert = isReading; }
    library.hidden = isReading; library.inert = isReading;
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
      if (!await beforeLeave()) return false;
      if (current === "reading") readingPosition = captureReading();
      else libraryY = window.scrollY;
      // Cancel an in-progress smooth scroll before replacing the visible surface.
      window.scrollTo({ top: window.scrollY, behavior: "instant" });
      current = page;
      revision += 1;
      const visit = revision;
      const isReading = current === "reading";
      display();
      onChange();
      const restore = () => {
        if (visit !== revision) return;
        const position = isReading && readingPosition?.saveId === saveId() ? readingPosition : null;
        const anchor = position?.selector && reading.querySelector(position.selector);
        const y = isReading ? (anchor ? window.scrollY + anchor.getBoundingClientRect().top - position.top : position?.y || 0) : libraryY;
        window.scrollTo({ top: y, behavior: "instant" });
        const focus = position?.focus;
        if (focus?.isConnected && !focus.disabled && !focus.closest("[hidden], [inert]")) focus.focus({ preventScroll: true });
        else document.getElementById(isReading ? "openJourneyLibraryBtn" : "journeyLibraryTitle").focus({ preventScroll: true });
      };
      restore();
      window.requestAnimationFrame(restore);
      return true;
    } finally {
      navigating = false;
    }
  }

  return {
    isReading: () => current === "reading",
    get revision() { return revision; },
    showLibrary: () => show("library"),
    showReading: () => show("reading"),
    invalidateReading: () => { readingPosition = null; },
  };
}
