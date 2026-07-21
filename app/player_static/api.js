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

async function readStream(response, onEvent) {
  if (!response.ok || !response.body) {
    throw new Error(`请求失败（${response.status}）`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let operationId = null;
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
      if (!event.ok) {
        const error = new Error(errorMessage(event));
        error.operationId = operationId;
        throw error;
      }
      if (event.step === "operation_started") {
        operationId = event.data?.operation_id || operationId;
      }
      if (event.step === "done") {
        await reader.cancel();
        return operationId;
      }
      onEvent(event);
    }
  }
  const error = new Error("任务连接已中断，刷新后将继续恢复。");
  error.operationId = operationId;
  throw error;
}

export async function acknowledgeOperation(operationId) {
  if (!operationId) return;
  await request(
    `/api/player/operations/${encodeURIComponent(operationId)}/ack`,
    { method: "POST" },
  );
}

export async function stream(path, body, onEvent) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return readStream(response, onEvent);
}

export async function subscribe(path, onEvent) {
  const response = await fetch(path, {
    headers: { Accept: "text/event-stream" },
  });
  return readStream(response, onEvent);
}
