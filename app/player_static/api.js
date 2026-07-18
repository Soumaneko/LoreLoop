function errorMessage(payload, fallback = "请求没有完成，请稍后重试。") {
  if (typeof payload?.error === "string") return payload.error;
  return payload?.error?.message || fallback;
}

export async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok) {
    throw new Error(errorMessage(payload, `请求失败（${response.status}）`));
  }
  return payload.data;
}

export async function stream(path, body, onEvent) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!response.ok || !response.body) {
    throw new Error(`请求失败（${response.status}）`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const line = chunk.split("\n").find((item) => item.startsWith("data: "));
      if (!line) continue;
      const event = JSON.parse(line.slice(6));
      if (!event.ok) throw new Error(errorMessage(event));
      if (event.step === "done") {
        await reader.cancel();
        return;
      }
      onEvent(event);
    }
  }
}
