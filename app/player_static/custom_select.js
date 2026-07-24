function element(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function closeSelect(wrapper) {
  const trigger = wrapper.querySelector(".codex-custom-select-trigger");
  const menu = wrapper.querySelector(".codex-custom-select-menu");
  if (menu) menu.hidden = true;
  if (trigger) {
    trigger.classList.remove("is-open");
    trigger.setAttribute("aria-expanded", "false");
  }
}

function closeOtherSelects(currentWrapper) {
  for (const wrapper of document.querySelectorAll(".codex-custom-select-wrapper")) {
    if (wrapper !== currentWrapper) closeSelect(wrapper);
  }
}

export function syncCustomSelect(selectElement) {
  if (!selectElement) return;
  const wrapper = selectElement.nextElementSibling;
  if (!wrapper?.classList.contains("codex-custom-select-wrapper")) return;
  const selected = selectElement.selectedOptions[0] || selectElement.options[0];
  const label = wrapper.querySelector(".codex-custom-select-label");
  const trigger = wrapper.querySelector(".codex-custom-select-trigger");
  if (label) label.textContent = selected?.textContent || "请选择";
  if (trigger) {
    trigger.disabled = selectElement.disabled;
    trigger.setAttribute("aria-disabled", String(selectElement.disabled));
  }
  wrapper.classList.toggle("is-disabled", selectElement.disabled);
  if (selectElement.disabled) closeSelect(wrapper);
}

export function syncCustomSelects(selectElements) {
  for (const selectElement of selectElements) syncCustomSelect(selectElement);
}

export function enhanceCustomSelect(selectElement) {
  if (!selectElement) return;
  if (selectElement.dataset.customEnhanced === "true") {
    syncCustomSelect(selectElement);
    return;
  }
  selectElement.dataset.customEnhanced = "true";
  selectElement.hidden = true;

  const wrapper = element("div", "codex-custom-select-wrapper");
  const trigger = element("button", "codex-custom-select-trigger");
  trigger.type = "button";
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute(
    "aria-label",
    selectElement.labels?.[0]?.textContent?.trim() || "选择选项",
  );
  trigger.append(
    element("span", "codex-custom-select-label"),
    element("span", "codex-custom-select-arrow", "▼"),
  );

  const menu = element("div", "codex-custom-select-menu");
  menu.setAttribute("role", "listbox");
  menu.hidden = true;
  wrapper.append(trigger, menu);
  selectElement.after(wrapper);

  const renderOptions = () => {
    menu.replaceChildren();
    for (const option of selectElement.options) {
      if (option.disabled) continue;
      const selected = option.value === selectElement.value;
      const item = element(
        "button",
        `codex-custom-select-option${selected ? " is-selected" : ""}`,
      );
      item.type = "button";
      item.dataset.value = option.value;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(selected));
      item.appendChild(element("span", "", option.textContent));
      if (selected) item.appendChild(element("span", "check-mark", "✦"));
      item.onclick = (event) => {
        event.stopPropagation();
        if (selectElement.disabled) return;
        selectElement.value = option.value;
        selectElement.dispatchEvent(new Event("change", { bubbles: true }));
        closeSelect(wrapper);
        syncCustomSelect(selectElement);
        trigger.focus();
      };
      menu.appendChild(item);
    }
  };

  const open = () => {
    if (selectElement.disabled) return;
    closeOtherSelects(wrapper);
    renderOptions();
    menu.hidden = false;
    trigger.classList.add("is-open");
    trigger.setAttribute("aria-expanded", "true");
    menu.querySelector(".is-selected, .codex-custom-select-option")?.focus();
  };

  trigger.onclick = (event) => {
    event.stopPropagation();
    if (menu.hidden) open();
    else closeSelect(wrapper);
  };
  trigger.onkeydown = (event) => {
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      open();
    }
  };
  menu.onkeydown = (event) => {
    const options = [...menu.querySelectorAll(".codex-custom-select-option")];
    const current = options.indexOf(document.activeElement);
    let target = null;
    if (event.key === "ArrowDown") target = options[Math.min(options.length - 1, current + 1)];
    if (event.key === "ArrowUp") target = options[Math.max(0, current - 1)];
    if (event.key === "Home") target = options[0];
    if (event.key === "End") target = options.at(-1);
    if (event.key === "Escape") {
      event.preventDefault();
      closeSelect(wrapper);
      trigger.focus();
      return;
    }
    if (target) {
      event.preventDefault();
      target.focus();
    }
  };
  wrapper.addEventListener("focusout", (event) => {
    if (!wrapper.contains(event.relatedTarget)) closeSelect(wrapper);
  });
  document.addEventListener("pointerdown", (event) => {
    if (!wrapper.contains(event.target)) closeSelect(wrapper);
  });

  syncCustomSelect(selectElement);
}
