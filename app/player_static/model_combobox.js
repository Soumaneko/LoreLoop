// Editable model ID with optional suggestions. Focus stays in the input.
export function createModelCombobox({ input, menu, onSelect }) {
  let models = [], active = -1, opened = false;
  const matches = () => {
    const q = input.value.trim().toLowerCase();
    return models.filter(m => !q || m.value.toLowerCase().includes(q) || m.label.toLowerCase().includes(q));
  };
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", menu.id);
  function close() {
    opened = false;
    menu.hidden = true;
    active = -1;
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }
  function choose(value) {
    input.value = value;
    close();
    onSelect?.(value);
  }
  function render() {
    menu.replaceChildren();
    const rows = matches();
    if (active >= rows.length) active = -1;
    rows.forEach((row, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.id = menu.id + "-" + index;
      button.className = "model-combobox-option";
      button.textContent = row.label;
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", String(index === active));
      button.tabIndex = -1;
      button.onclick = () => choose(row.value);
      menu.append(button);
      if (index === active) {
        input.setAttribute("aria-activedescendant", button.id);
        button.scrollIntoView({block: "nearest"});
      }
    });
    if (!rows.length) {
      const hint = document.createElement("p");
      hint.className = "model-combobox-empty";
      hint.textContent = "没有候选，可直接填写完整模型 ID";
      menu.append(hint);
    }
    if (active < 0) input.removeAttribute("aria-activedescendant");
  }
  function open() {
    if (input.disabled) return;
    opened = true;
    render();
    menu.hidden = false;
    input.setAttribute("aria-expanded", "true");
  }
  input.addEventListener("focus", open);
  input.addEventListener("input", () => { active = -1; open(); });
  input.addEventListener("keydown", event => {
    if (event.key === "Enter") {
      event.preventDefault();
      event.stopPropagation();
      const row = opened && matches()[active];
      if (row) choose(row.value);
      else close();
    } else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!opened) open();
      active = Math.max(0, Math.min(matches().length - 1, active + (event.key === "ArrowDown" ? 1 : -1)));
      render();
    } else if (event.key === "Escape" && opened) {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === "Tab") close();
  });
  menu.addEventListener("mousedown", event => event.preventDefault());
  input.addEventListener("blur", close);
  return { setModels(rows) { models = rows || []; active = -1; if (opened) render(); }, close, open, choose };
}
