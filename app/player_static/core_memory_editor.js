import { loadCoreMemory, saveCoreMemory } from "./api.js";

const CORE_MEMORY_KIND_OPTIONS = [
  ["world_invariant", "世界恒常"],
  ["protagonist_invariant", "主角恒常"],
  ["narrative_preference", "叙事偏好"],
  ["system_convention", "系统惯例"],
  ["ability_rule", "能力规则"],
];
const CORE_MEMORY_STRENGTH_OPTIONS = [
  ["hard_constraint", "硬约束"],
  ["strong_preference", "强偏好"],
];
const CORE_MEMORY_SOURCE_LABELS = { player: "玩家", campaign_setup: "世界构建" };

export function createCoreMemoryEditor({ node, canWrite, onBusyChange, showToast, showError }) {
  const els = Object.fromEntries([
    "coreMemoryDialog", "closeCoreMemoryBtn", "addCoreMemoryBtn", "saveCoreMemoryBtn",
    "coreMemoryList", "coreMemoryError",
  ].map(id => [id, document.getElementById(id)]));
  let coreMemoryItems = [];
  let initialized = false;
  let busy = false;

  function refreshControls() {
    const writable = initialized && !busy && canWrite();
    for (const control of els.coreMemoryList.querySelectorAll("input, textarea, select, button")) {
      control.disabled = !writable;
    }
    els.addCoreMemoryBtn.disabled = !writable;
    els.saveCoreMemoryBtn.disabled = !writable;
    els.closeCoreMemoryBtn.disabled = busy;
  }

  function setBusy(value) {
    busy = value;
    refreshControls();
    onBusyChange();
  }

  function _buildCoreMemorySelect(options, value) {
    const select = node("select");
    for (const [key, label] of options) {
      const option = node("option", "", label);
      option.value = key;
      if (key === value) option.selected = true;
      select.appendChild(option);
    }
    return select;
  }

  function renderCoreMemoryItems(items) {
    els.coreMemoryList.replaceChildren();
    if (!items.length) {
      els.coreMemoryList.appendChild(node("p", "core-memory-empty", "暂无条目。点击「添加条目」开始构建世界基础。"));
      return;
    }
    for (const item of items) {
      const row = node("div", "core-memory-item");
      const head = node("div", "core-memory-item-head");
      const kindSelect = _buildCoreMemorySelect(CORE_MEMORY_KIND_OPTIONS, item.kind);
      kindSelect.addEventListener("change", () => { item.kind = kindSelect.value; });
      const strengthSelect = _buildCoreMemorySelect(CORE_MEMORY_STRENGTH_OPTIONS, item.strength);
      strengthSelect.addEventListener("change", () => { item.strength = strengthSelect.value; });
      head.appendChild(kindSelect);
      head.appendChild(strengthSelect);
      head.appendChild(node("span", "core-memory-item-source", CORE_MEMORY_SOURCE_LABELS[item.source] || item.source || ""));
      const removeBtn = node("button", "remove-core-memory-btn", "删除");
      removeBtn.type = "button";
      removeBtn.onclick = () => {
        if (busy || !canWrite()) return;
        const index = items.indexOf(item);
        if (index >= 0) items.splice(index, 1);
        renderCoreMemoryItems(items);
      };
      head.appendChild(removeBtn);
      row.appendChild(head);
      const textarea = node("textarea");
      textarea.value = item.content || "";
      textarea.rows = 3;
      textarea.addEventListener("input", () => { item.content = textarea.value; });
      row.appendChild(textarea);
      els.coreMemoryList.appendChild(row);
    }
  }

  async function open() {
    if (busy || els.coreMemoryDialog.open) return;
    setBusy(true);
    els.coreMemoryError.hidden = true;
    try {
      const payload = await loadCoreMemory();
      initialized = Boolean(payload.initialized);
      coreMemoryItems = initialized ? (payload.items || []).map(item => ({ ...item })) : [];
      renderCoreMemoryItems(coreMemoryItems);
      if (!initialized) {
        els.coreMemoryError.textContent = "核心记忆尚未初始化，请先完成世界初始化。";
        els.coreMemoryError.hidden = false;
      }
      els.coreMemoryDialog.showModal();
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (busy || !initialized || !canWrite()) return;
    setBusy(true);
    try {
      const payload = await saveCoreMemory(coreMemoryItems);
      coreMemoryItems = (payload.items || []).map(item => ({ ...item }));
      renderCoreMemoryItems(coreMemoryItems);
      els.coreMemoryError.hidden = true;
      showToast("核心记忆已更新");
    } catch (error) {
      els.coreMemoryError.textContent = error?.message || "保存失败";
      els.coreMemoryError.hidden = false;
    } finally {
      setBusy(false);
    }
  }

  function close() {
    if (busy) return;
    els.coreMemoryDialog.close();
  }

  function bind() {
    els.closeCoreMemoryBtn.onclick = close;
    els.coreMemoryDialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
    els.addCoreMemoryBtn.onclick = () => {
      if (busy || !initialized || !canWrite()) return;
      coreMemoryItems.push({ id: "", kind: "narrative_preference", strength: "strong_preference", source: "player", content: "" });
      renderCoreMemoryItems(coreMemoryItems);
      refreshControls();
    };
    els.saveCoreMemoryBtn.onclick = save;
  }

  return { bind, open, refreshControls, isBusy: () => busy };
}
