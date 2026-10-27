export function createManagementWorkspace({ pages, beforeLeave, onLeave, canNavigate }) {
  const drawer = document.getElementById("drawer");
  const back = document.getElementById("workspaceBackBtn");
  const close = document.getElementById("closeDrawerBtn");
  const entrance = document.getElementById("openDrawerBtn");
  const backdrop = document.getElementById("drawerBackdrop");
  const positions = new Map();
  let current = "systemControlHome";
  let navigating = false;
  const background = [...document.body.children].filter(element => element !== drawer && element !== backdrop
    && element.tagName !== "DIALOG" && !element.hasAttribute("popover") && element.id !== "toast");
  let previousInert = [];
  drawer.inert = true;

  document.addEventListener("keydown", event => {
    if (event.key !== "Tab" || !drawer.classList.contains("open") || document.querySelector("dialog[open], [popover]:popover-open")) return;
    const controls = [...drawer.querySelectorAll("button, input, select, textarea, a[href], [tabindex='0']")]
      .filter(element => !element.disabled && element.getClientRects().length > 0);
    const first = controls[0], last = controls.at(-1);
    if (!first || (event.shiftKey && document.activeElement === first) || (!event.shiftKey && document.activeElement === last)) {
      event.preventDefault();
      (event.shiftKey ? last : first)?.focus();
    }
  });

  function refresh() {
    const blocked = navigating || !canNavigate();
    back.disabled = blocked;
    close.disabled = blocked;
  }

  function show(id = current) {
    const changed = id !== current;
    if (changed) {
      const previous = document.getElementById(current);
      positions.set(current, { focus: document.activeElement, scroll: previous.querySelector(".workspace-content")?.scrollTop ?? previous.scrollTop });
      previous.hidden = true;
      onLeave(current);
      current = id;
    }
    const page = document.getElementById(current);
    const info = pages[current];
    page.hidden = false;
    drawer.dataset.workspacePage = current;
    if (info.width) drawer.style.setProperty("--workspace-width", `${info.width}px`);
    else drawer.style.removeProperty("--workspace-width");
    document.getElementById("systemControlTitle").textContent = info.title;
    back.hidden = !info.parent;
    back.textContent = info.parent ? `‹ 返回${pages[info.parent].title}` : "";
    if (!drawer.classList.contains("open")) {
      previousInert = background.map(element => element.inert);
      background.forEach(element => { element.inert = true; });
    }
    drawer.inert = false;
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    entrance.setAttribute("aria-expanded", "true");
    backdrop.hidden = false;
    refresh();
    const position = positions.get(current);
    const body = page.querySelector(".workspace-content") || page;
    if (changed) body.scrollTop = position?.scroll || 0;
    const focus = position?.focus;
    if (changed && focus?.isConnected && page.contains(focus) && !focus.disabled) focus.focus({ preventScroll: true });
    else (info.parent ? back : close).focus({ preventScroll: true });
  }

  async function leave(destination) {
    if (navigating || !canNavigate()) return false;
    navigating = true;
    refresh();
    try {
      if (!await beforeLeave(current)) return false;
      if (destination) show(destination);
      else {
        show("systemControlHome");
        drawer.classList.remove("open");
        drawer.inert = true;
        background.forEach((element, index) => { element.inert = previousInert[index]; });
        drawer.setAttribute("aria-hidden", "true");
        entrance.setAttribute("aria-expanded", "false");
        backdrop.hidden = !document.getElementById("leftDrawer").classList.contains("open");
        entrance.focus({ preventScroll: true });
      }
      return true;
    } finally {
      navigating = false;
      refresh();
    }
  }

  return {
    show,
    refresh,
    isOpen: id => current === id && drawer.classList.contains("open"),
    back: () => leave(pages[current].parent || null),
    close: () => leave(null),
  };
}
