export function createRuntimeNotices({ state, $, document }) {
  function renderRuntimeNotices() {
    const panel = $("runtimeNotices");
    const notices = state.runtimeNotices || [];
    panel.hidden = notices.length === 0;
    $("runtimeNoticeSummary").textContent = notices.length
      ? `近期运行提示（${notices.length}）：${notices[0].message.split("\n")[0]}` : "近期运行提示";
    $("runtimeNoticeList").replaceChildren(...notices.map(notice => {
      const item = document.createElement("li");
      item.textContent = `${new Date(notice.created_at).toLocaleString()} · ${notice.message}`;
      return item;
    }));
  }

  function handleRuntimeNotice(event) {
    if (event.step !== "runtime_notice") return false;
    const notice = event.data;
    state.runtimeNotices = [notice, ...(state.runtimeNotices || []).filter(item => item.notice_id !== notice.notice_id)].slice(0, 20);
    renderRuntimeNotices();
    return true;
  }
  return { renderRuntimeNotices, handleRuntimeNotice };
}
