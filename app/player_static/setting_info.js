import { bindPopoverFallbackDismiss } from "./popover_fallback.js";

function positionNear(dialog, anchor) {
  const rect = anchor.getBoundingClientRect();
  const dialogRect = dialog.getBoundingClientRect();
  let top = rect.bottom + 8;
  let left = rect.left - dialogRect.width / 2 + rect.width / 2;
  if (left < 10) left = 10;
  if (left + dialogRect.width > window.innerWidth - 10) {
    left = window.innerWidth - dialogRect.width - 10;
  }
  if (top + dialogRect.height > window.innerHeight - 10) {
    top = Math.max(10, rect.top - dialogRect.height - 8);
  }
  dialog.style.top = `${top}px`;
  dialog.style.left = `${left}px`;
}

export function createSettingInfoController({ dialog, titleEl, contentEl }) {
  const native = () => typeof dialog?.showPopover === "function";
  const setContent = (title, body) => {
    titleEl.textContent = title;
    contentEl.textContent = body;
  };
  const show = (anchor, { title, body }) => {
    if (!dialog) return;
    setContent(title, body);
    if (native()) {
      if (dialog.hidePopover) dialog.hidePopover();
      dialog.showPopover();
    } else {
      dialog.setAttribute("open", "");
    }
    if (anchor && typeof anchor.getBoundingClientRect === "function") {
      positionNear(dialog, anchor);
    }
  };
  const hide = () => {
    if (!dialog) return;
    if (native() && dialog.hidePopover) dialog.hidePopover();
    else dialog.removeAttribute("open");
  };
  return { setContent, show, hide };
}

export function bindSettingInfoButtons({ dialog, titleEl, contentEl, buttons, specs }) {
  const controller = createSettingInfoController({ dialog, titleEl, contentEl });
  for (const [id, spec] of Object.entries(specs)) {
    const button = buttons?.[id];
    if (button) {
      button.onclick = (e) => {
        if (e?.stopPropagation) e.stopPropagation();
        controller.show(button, spec);
      };
    }
  }
  return controller;
}

export function bindSettingInfoDismiss(dialog, onHide) {
  dialog.addEventListener("click", (e) => {
    if (e.target === dialog) onHide();
  });
  bindPopoverFallbackDismiss(dialog, onHide);
}
