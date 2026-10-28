import { request, downloadSave, importSaveArchive } from "./api.js";

export function createJourneyLibrary({ state, canManage, canContinue, onBusyChange, onEnter, onSync,
  onModels, showConfirm, showToast, reportError }) {
  const $ = id => document.getElementById(id);
  const content = $("journeyLibraryContent");
  const menu = $("journeyMenu");
  const dialog = $("renameJourneyDialog");
  let activeId = null, loaded = false, busy = false, error = "", task = null;
  let menuSave = null, renameSave = null, entryRetry = null, listRetry = false;
  const node = (tag, className = "", text) => {
    const el = document.createElement(tag); el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  };
  const button = (text, className, action) => {
    const el = node("button", className, text); el.type = "button"; el.onclick = action; return el;
  };
  const post = (action, payload) => request(`/api/player/saves/${action}`, { method: "POST", body: JSON.stringify(payload) });
  function icon(path) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true");
    const line = document.createElementNS(svg.namespaceURI, "path"); line.setAttribute("d", path); svg.append(line); return svg;
  }
  function setBusy(value) { busy = value; onBusyChange(value); refreshControls(); }
  function closeMenu() {
    menu.hidden = true;
    document.querySelector('[data-journey-menu][aria-expanded="true"]')?.setAttribute("aria-expanded", "false");
    menuSave = null;
  }
  function errorMessage(cause, prefix = "") {
    reportError(cause); error = `${prefix}${cause.message || "操作没有完成，请重试。"}`;
    const el = task ? $("journeyTaskError") : $("journeyLibraryError");
    el.textContent = error; el.hidden = false;
  }
  function update(payload) {
    if (payload.saves) state.saves = payload.saves;
    activeId = payload.active ?? null; loaded = true;
    if (!task) render();
  }
  async function refresh() {
    if (busy) return null;
    setBusy(true); error = "";
    try {
      const payload = await request("/api/player/saves");
      listRetry = false; update(payload); return payload;
    } catch (cause) {
      listRetry = true; reportError(cause); error = cause.message || "暂时无法读取旅程，请重试。";
      render(); throw cause;
    } finally { setBusy(false); }
  }
  function metadata(save) {
    const meta = node("div", "journey-meta");
    const values = [save.protagonist_name, save.initialized ? `已确认 ${save.turn_count || 0} 回合` : "创建未完成", save.location].filter(Boolean);
    for (const value of values) meta.append(node("span", "", value));
    return meta;
  }
  function lastPlayed(save) {
    if (!save.last_played_at) return "";
    const date = new Date(save.last_played_at);
    return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("zh-CN", {
      month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
    }).format(date);
  }
  function more(save) {
    const el = button("", "journey-more", event => {
      event.stopPropagation();
      if (menuSave?.save_id === save.save_id) { closeMenu(); return; }
      closeMenu(); menuSave = save; menu.replaceChildren();
      for (const [label, action] of [["重命名", () => rename(save)], ["复制旅程", () => mutate(() => post("copy", { save_id: save.save_id }), "旅程副本已创建")],
        ["导出 ZIP", () => exportSave(save)], ["删除旅程", () => remove(save)]]) {
        const item = button(label, label === "删除旅程" ? "journey-menu-danger" : "", () => { closeMenu(); action(); });
        item.setAttribute("role", "menuitem");
        if (label === "删除旅程" && state.saves.length <= 1) { item.disabled = true; item.title = "至少保留一段旅程"; }
        menu.append(item);
      }
      menu.hidden = false; el.setAttribute("aria-expanded", "true");
      const rect = el.getBoundingClientRect();
      menu.style.left = `${Math.max(12, Math.min(rect.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 12))}px`;
      menu.style.top = `${Math.max(12, Math.min(rect.bottom + 6, window.innerHeight - menu.offsetHeight - 12))}px`;
      menu.querySelector("button:not(:disabled)").focus();
    });
    el.dataset.journeyMenu = save.save_id; el.dataset.libraryWrite = "";
    el.setAttribute("aria-label", `${save.name}的更多操作`); el.setAttribute("aria-haspopup", "menu"); el.setAttribute("aria-expanded", "false");
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("aria-hidden", "true");
    for (const x of [5, 12, 19]) { const dot = document.createElementNS(svg.namespaceURI, "circle"); dot.setAttribute("cx", x); dot.setAttribute("cy", "12"); dot.setAttribute("r", "1"); svg.append(dot); }
    el.append(svg); return el;
  }
  function render() {
    if (task) return;
    closeMenu(); content.replaceChildren();
    const heading = node("div", "journey-heading"); const title = node("h1", "", "旅程库");
    title.id = "journeyLibraryTitle"; title.tabIndex = -1; heading.append(title);
    const actions = node("div", "journey-heading-actions");
    for (const [label, action] of [["新建旅程", () => openTask("new")], ["导入存档", () => $("importSaveInput").click()]]) {
      const el = button(label, label === "新建旅程" ? "journey-secondary" : "journey-quiet", action);
      el.dataset.libraryWrite = ""; el.id = label === "新建旅程" ? "newJourneyBtn" : "importSaveBtn"; actions.append(el);
      el.prepend(icon(label === "新建旅程" ? "M12 5v14M5 12h14" : "M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5"));
    }
    if (loaded && state.saves.length) heading.append(actions);
    content.append(heading);
    const alert = node("div", "journey-notice"); alert.id = "journeyLibraryError"; alert.setAttribute("role", "alert"); alert.hidden = !error;
    alert.textContent = error; content.append(alert);
    if (listRetry || entryRetry) {
      const retry = button("重新读取", "journey-secondary", async () => {
        if (entryRetry) await retryEntry(); else { try { await refresh(); } catch (_) { /* The library retains its error. */ } }
      }); retry.id = "retryJourneysBtn"; content.append(retry);
    }
    if (!loaded) { content.append(node("p", "journey-help", listRetry ? "旅程暂时无法显示，已保存的内容不会丢失。" : "正在读取旅程……")); refreshControls(); return; }
    const noModel = node("section", "journey-notice"); noModel.id = "journeyModelNotice";
    noModel.append(node("p", "", "尚未配置模型服务。你仍可管理存档，开始游玩前请先完成配置。"), button("配置模型服务", "journey-quiet", () => onModels()));
    content.append(noModel);
    if (!state.saves.length) {
      const empty = node("section", "journey-empty"); empty.append(node("h2", "", "开始你的第一段旅程"), node("p", "", "创建世界与主角，或导入已有存档，接着上次的故事。"), actions);
      content.append(empty);
    } else {
      const current = state.saves.find(save => save.save_id === activeId);
      if (current) {
        const featured = node("section", "journey-featured");
        const top = node("div", "journey-featured-head"); const names = node("div");
        const nameLine = node("div", "journey-featured-title"); const name = node("h2", "", current.name); name.id = "journeyLibraryName";
        nameLine.append(name, node("span", "journey-current-tag", "当前旅程")); names.append(nameLine, metadata(current)); top.append(names, more(current));
        const status = node("p", "journey-status"); status.id = "journeyLibraryStatus"; status.setAttribute("role", "status");
        const footer = node("div", "journey-featured-footer"); const enter = button("继续旅程", "primary-action", () => enterJourney(current)); enter.id = "continueJourneyBtn";
        const played = lastPlayed(current);
        footer.append(node("span", "journey-played", played ? `最后游玩 · ${played}` : ""), enter); featured.append(top, status, footer); content.append(featured);
      }
      const others = state.saves.filter(save => save.save_id !== activeId);
      if (others.length) {
        const section = node("div", "journey-list-heading"); section.append(node("h2", "", current ? "其他旅程" : "你的旅程"), node("span", "journey-played", `${others.length} 段`)); content.append(section);
        for (const save of others) {
          const row = node("article", "journey-row"); row.dataset.saveId = save.save_id;
          const enter = button("", "journey-row-main", () => enterJourney(save)); enter.dataset.libraryWrite = "";
          const text = node("span"); text.append(node("strong", "journey-row-title", save.name), metadata(save));
          const end = node("span", "journey-row-end"); end.append(node("span", "journey-played", lastPlayed(save)), node("span", "journey-arrow", "›")); enter.append(text, end);
          row.append(enter, more(save)); content.append(row);
        }
      }
    }
    refreshControls();
  }
  function refreshControls() {
    for (const el of document.querySelectorAll("[data-library-write]")) el.disabled = busy || !canManage();
    for (const el of content.querySelectorAll("input, button")) {
      if (el.id !== "continueJourneyBtn" && !el.hasAttribute("data-library-write")) el.disabled = busy;
    }
    $("journeyModelNotice")?.toggleAttribute("hidden", Boolean(state.modelConfig?.configured));
    const enter = $("continueJourneyBtn"), status = $("journeyLibraryStatus");
    if (enter) {
      const current = state.saves.find(save => save.save_id === activeId);
      const uncertain = state.uncertainConfirmation?.saveId === state.activeSave;
      enter.disabled = busy || !canContinue();
      enter.textContent = !state.modelConfig?.configured ? "配置后继续" : uncertain ? "核实保存结果"
        : ["generating", "confirming"].includes(state.mode) ? "查看进度" : current?.initialized ? "继续旅程" : "继续创建";
      status.textContent = uncertain ? "这一回合是否已保存还未确定，请先核实。" : state.mode === "generating" ? "故事正在生成中，你可以查看当前进度。"
        : state.mode === "confirming" ? "正在保存这一回合，请稍候。" : !current?.initialized ? "创建尚未完成，可以从上次的位置继续。" : "";
      status.hidden = !status.textContent;
    }
    if (dialog.open) for (const el of dialog.querySelectorAll("input,button")) el.disabled = busy;
    if (task && entryRetry) for (const el of content.querySelectorAll("input, button[type='submit']")) el.disabled = true;
    $("libraryModelMark").textContent = state.modelConfig?.configured ? "已配置" : "未配置";
  }
  async function canLeave() {
    if (busy) return false;
    if (!task || entryRetry || !$("journeyTaskName").value.trim()) return true;
    const accepted = await showConfirm({ title: "放弃填写？", message: "已填写的旅程名称还没有保存。", confirmText: "放弃填写", cancelText: "继续填写" });
    if (accepted) task = null;
    return accepted;
  }
  async function home() {
    const kind = task?.kind;
    if (!await canLeave()) return false;
    task = null; error = ""; render();
    if (kind) $(kind === "new" ? "newJourneyBtn" : "importSaveBtn")?.focus({ preventScroll: true });
    return true;
  }
  function openTask(kind, file = null) {
    if (busy || !canManage()) return;
    task = { kind, file }; error = ""; closeMenu(); content.replaceChildren();
    const back = button("‹ 返回旅程库", "journey-quiet journey-task-back", home); const title = node("h1", "", kind === "new" ? "新建旅程" : "导入旅程"); title.id = "journeyLibraryTitle"; title.tabIndex = -1;
    const form = node("form", "journey-task-form");
    if (file) form.append(node("p", "journey-file", file.name));
    const label = node("label", "", "旅程名称"); const input = node("input"); input.id = "journeyTaskName"; input.required = true; input.maxLength = 80; input.autocomplete = "off"; input.placeholder = "为这段旅程起一个名字";
    input.value = file ? file.name.replace(/\.zip$/i, "") : ""; label.append(input);
    const help = node("p", "journey-help", kind === "new" ? "接下来填写世界与主角，名称之后也可以修改。" : "导入后进入这段旅程，已有存档不会被覆盖。");
    const alert = node("p", "journey-form-error"); alert.id = "journeyTaskError"; alert.hidden = true; alert.setAttribute("role", "alert");
    const actions = node("div", "journey-task-actions"); const submit = node("button", "primary-action", kind === "new" ? "创建并继续" : "导入并进入"); submit.type = "submit"; actions.append(button("取消", "journey-secondary", home), submit);
    form.append(label, help, alert, actions); content.append(back, title, form);
    form.onsubmit = event => {
      event.preventDefault(); const name = input.value.trim(); if (!name || busy || entryRetry) return;
      mutate(() => kind === "new" ? post("create", { name, activate: true }) : importSaveArchive(file, name), kind === "new" ? "旅程已创建" : "旅程已导入", true);
    };
    input.focus(); refreshControls();
  }
  async function enterJourney(save) {
    if (busy) return;
    try {
      await onEnter(save.save_id, activeId === save.save_id);
      if (entryRetry === save.save_id) { entryRetry = null; error = ""; render(); }
    }
    catch (cause) { errorMessage(cause); }
  }
  async function retryEntry() {
    if (!entryRetry || busy) return;
    const id = entryRetry;
    setBusy(true);
    try {
      await onSync(); task = null; entryRetry = null; error = ""; render(); setBusy(false);
      await onEnter(id, activeId === id);
    } catch (cause) {
      entryRetry = id;
      errorMessage(cause, "旅程已保存，但暂时无法打开。请重试。\n");
      if (!task) render();
    }
    finally { setBusy(false); }
  }
  async function mutate(write, message, enter = false) {
    if (busy || !canManage()) return;
    setBusy(true); closeMenu(); let committed = false;
    try {
      const payload = await write(); committed = true; update(payload);
      if (enter) entryRetry = payload.save.save_id;
      if (payload.active !== state.activeSave) await onSync(payload);
      if (enter) {
        const id = entryRetry; task = null; entryRetry = null; error = ""; render(); setBusy(false);
        try { await onEnter(id, payload.active === id); }
        catch (cause) { entryRetry = id; throw cause; }
      } else { error = ""; render(); }
      showToast(message);
    } catch (cause) {
      errorMessage(cause, committed ? `${message}，但暂时无法打开。请重试。\n` : "");
      if (committed) {
        if (task) { const retry = button("重新读取", "journey-secondary", retryEntry); retry.id = "retryJourneyEntryBtn"; content.append(retry); }
        else { if (!entryRetry) listRetry = true; render(); }
      }
    } finally { setBusy(false); }
  }
  function rename(save) {
    if (busy || !canManage()) return;
    renameSave = save; $("renameJourneyInput").value = save.name; $("renameJourneyError").hidden = true; dialog.showModal(); $("renameJourneyInput").focus();
  }
  async function remove(save) {
    if (busy || !canManage() || state.saves.length <= 1) return;
    const accepted = await showConfirm({ title: "删除这段旅程？", message: `将永久删除“${save.name}”及其全部进度，此操作无法撤销。`, confirmText: "永久删除", isDanger: true });
    if (!accepted || !canManage() || busy || !state.saves.some(item => item.save_id === save.save_id)) return;
    const replacement = state.saves.find(item => item.save_id !== save.save_id);
    await mutate(() => post("delete", { save_id: save.save_id, confirmation_name: save.name, replacement_save_id: replacement.save_id }), "旅程已删除");
  }
  async function exportSave(save) {
    if (busy || !canManage()) return;
    setBusy(true);
    try {
      const archive = await downloadSave(save.save_id); const url = URL.createObjectURL(archive.blob); const link = node("a"); link.href = url; link.download = archive.filename;
      document.body.append(link); link.click(); link.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 0); showToast("存档已导出");
    } catch (cause) { errorMessage(cause); } finally { setBusy(false); }
  }
  function bind() {
    $("importSaveInput").onchange = () => { const file = $("importSaveInput").files?.[0]; $("importSaveInput").value = ""; if (file) openTask("import", file); };
    $("cancelRenameJourneyBtn").onclick = () => { if (!busy) dialog.close(); };
    dialog.addEventListener("cancel", event => { if (busy) event.preventDefault(); });
    dialog.addEventListener("close", () => document.querySelector(`[data-journey-menu="${renameSave?.save_id}"]`)?.focus({ preventScroll: true }));
    $("renameJourneyForm").onsubmit = async event => {
      event.preventDefault(); const name = $("renameJourneyInput").value.trim();
      if (!name || busy || !canManage()) return;
      setBusy(true);
      try { update(await post("rename", { save_id: renameSave.save_id, name })); dialog.close(); showToast("旅程名称已更新"); }
      catch (cause) { reportError(cause); $("renameJourneyError").textContent = cause.message || "名称没有保存，请重试。"; $("renameJourneyError").hidden = false; }
      finally { setBusy(false); }
    };
    document.addEventListener("click", event => { if (!menu.contains(event.target) && !event.target.closest("[data-journey-menu]")) closeMenu(); });
    menu.onkeydown = event => {
      const items = [...menu.querySelectorAll("button:not(:disabled)")]; const index = items.indexOf(document.activeElement);
      if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        event.preventDefault(); items[event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length]?.focus();
      }
    };
    document.addEventListener("keydown", event => { if (event.key === "Escape" && !menu.hidden) { event.preventDefault(); const id = menuSave?.save_id; closeMenu(); document.querySelector(`[data-journey-menu="${id}"]`)?.focus(); } });
  }
  return { bind, update, refresh, render, refreshControls, canLeave, home, closeMenu,
    hasUnsavedChanges: () => Boolean(task && !entryRetry && $("journeyTaskName")?.value.trim())
      || Boolean(dialog.open && $("renameJourneyInput").value !== renameSave?.name),
  };
}
