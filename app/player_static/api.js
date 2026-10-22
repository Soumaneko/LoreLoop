export class ApiError extends Error {
  constructor(message, {
    status = 0,
    code = "request_failed",
    retryable = false,
    operationId = null,
    details = null,
  } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
    this.operationId = operationId;
    this.details = details;
  }
}

function errorDetails(payload, fallback = "请求没有完成，请稍后重试。") {
  const error = payload?.error;
  if (typeof error === "string") {
    return { message: error, code: "request_failed", retryable: false };
  }
  return {
    message: error?.message || fallback,
    code: error?.code || "request_failed",
    retryable: Boolean(error?.recoverable ?? error?.retryable),
    details: error?.details || null,
  };
}

function apiError(payload, {
  status = 0,
  fallback,
  operationId = null,
} = {}) {
  const details = errorDetails(payload, fallback);
  return new ApiError(details.message, {
    status,
    code: details.code,
    retryable: details.retryable,
    operationId,
    details: details.details,
  });
}

export function desktopHeaders(headers = {}) {
  const token = document
    .querySelector('meta[name="loreloop-session-token"]')
    ?.getAttribute("content") || "";
  return token
    ? { ...headers, "X-LoreLoop-Token": token }
    : { ...headers };
}

async function fetchWithTimeout(path, options, timeoutMs) {
  const controller = new AbortController();
  const externalSignal = options.signal;
  const abortFromExternal = () => controller.abort(externalSignal.reason);
  if (externalSignal) {
    if (externalSignal.aborted) abortFromExternal();
    else externalSignal.addEventListener("abort", abortFromExternal, { once: true });
  }
  const timer = window.setTimeout(
    () => controller.abort(new DOMException("Request timed out", "TimeoutError")),
    timeoutMs,
  );
  try {
    return await fetch(path, {
      ...options,
      headers: desktopHeaders(options.headers),
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted) {
      const timedOut = controller.signal.reason?.name === "TimeoutError";
      throw new ApiError(
        timedOut ? "请求超时，请检查本地服务或模型连接。" : "请求已取消。",
        {
          code: timedOut ? "request_timeout" : "request_cancelled",
          retryable: timedOut,
        },
      );
    }
    throw new ApiError(error?.message || "无法连接本地服务。", {
      code: "connection_failed",
      retryable: true,
    });
  } finally {
    window.clearTimeout(timer);
    externalSignal?.removeEventListener("abort", abortFromExternal);
  }
}

export async function request(path, {
  timeoutMs = 60_000,
  headers = {},
  ...options
} = {}) {
  const response = await fetchWithTimeout(path, {
    headers: { "Content-Type": "application/json", ...headers },
    ...options,
  }, timeoutMs);
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok) {
    throw apiError(payload, {
      status: response.status,
      fallback: `请求失败（${response.status}）`,
    });
  }
  return payload.data;
}

async function responseError(response, fallback) {
  const payload = await response.json().catch(() => null);
  return apiError(payload, {
    status: response.status,
    fallback: fallback || `请求失败（${response.status}）`,
  });
}

export async function downloadSave(saveId) {
  const response = await fetch(
    `/api/player/saves/export?save_id=${encodeURIComponent(saveId)}`,
    { headers: desktopHeaders() },
  );
  if (!response.ok) throw await responseError(response);
  const disposition = response.headers.get("Content-Disposition") || "";
  const utf8Name = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plainName = disposition.match(/filename="?([^";]+)"?/i)?.[1];
  let filename = plainName || "save.zip";
  if (utf8Name) {
    try { filename = decodeURIComponent(utf8Name); } catch (_error) { /* Use fallback. */ }
  }
  filename = filename.split(/[\\/]/).pop() || "save.zip";
  return {
    blob: await response.blob(),
    filename,
  };
}

export async function importSaveArchive(file, name) {
  const response = await fetch(
    `/api/player/saves/import?name=${encodeURIComponent(name)}`,
    {
      method: "POST",
      headers: desktopHeaders({ "Content-Type": "application/zip" }),
      body: file,
    },
  );
  const payload = await response.json().catch(() => null);
  if (!response.ok || !payload?.ok) {
    throw apiError(payload, {
      status: response.status,
      fallback: `导入失败（${response.status}）`,
    });
  }
  return payload.data;
}

export function parseSSEFrames(buffer, { flush = false } = {}) {
  const normalized = buffer.replace(/\r\n/g, "\n");
  const blocks = normalized.split("\n\n");
  const remainder = flush ? "" : blocks.pop() || "";
  if (flush && blocks.at(-1) === "") blocks.pop();
  const frames = [];
  for (const block of blocks) {
    const data = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data) continue;
    try {
      frames.push(JSON.parse(data));
    } catch (error) {
      throw new ApiError("任务事件格式无效，刷新后可尝试恢复。", {
        code: "stream_protocol_error",
        retryable: true,
        details: { cause: error.message },
      });
    }
  }
  if (flush && normalized.trim() && !normalized.includes("\n\n")) {
    return parseSSEFrames(`${normalized}\n\n`);
  }
  return { frames, remainder };
}

async function readStream(response, onEvent) {
  if (!response.ok || !response.body) {
    throw await responseError(response);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let operationId = null;

  const handleFrames = async (frames) => {
    for (const event of frames) {
      if (!event.ok) {
        throw apiError(event, { operationId });
      }
      if (event.step === "operation_started") {
        operationId = event.data?.operation_id || operationId;
      }
      if (event.step === "done") {
        await reader.cancel();
        return true;
      }
      await onEvent(event);
    }
    return false;
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      buffer += decoder.decode();
      const parsed = parseSSEFrames(buffer, { flush: true });
      if (await handleFrames(parsed.frames)) return operationId;
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSSEFrames(buffer);
    buffer = parsed.remainder;
    if (await handleFrames(parsed.frames)) return operationId;
  }
  throw new ApiError("任务连接已中断，刷新后将继续恢复。", {
    code: "operation_stream_interrupted",
    retryable: true,
    operationId,
  });
}

export async function acknowledgeOperation(operationId) {
  if (!operationId) return;
  await request(
    `/api/player/operations/${encodeURIComponent(operationId)}/ack`,
    { method: "POST" },
  );
}

export async function loadRecords(view = "general") {
  return request(`/api/player/records?view=${encodeURIComponent(view)}`, { timeoutMs: 30_000 });
}

export async function updateRecord(kind, recordId, payload, view = "general") {
  return request("/api/player/records/update", {
    method: "POST",
    body: JSON.stringify({
      kind,
      record_id: recordId,
      view,
      payload: payload || {},
    }),
  });
}

export async function loadEvents() {
  return request("/api/player/events", { timeoutMs: 30_000 });
}

export async function loadCoreMemory() {
  return request("/api/player/core-memory", { timeoutMs: 30_000 });
}

export async function saveCoreMemory(items) {
  return request("/api/player/core-memory", {
    method: "POST",
    body: JSON.stringify({ items }),
  });
}

export async function saveExperienceMode(mode) {
  return request("/api/player/experience-mode", {
    method: "POST",
    body: JSON.stringify({ experience_mode: mode }),
  });
}

export async function cancelOperation(operationId) {
  return request(
    `/api/player/operations/${encodeURIComponent(operationId)}/cancel`,
    { method: "POST" },
  );
}

export async function stream(path, body, onEvent) {
  const response = await fetch(path, {
    method: "POST",
    headers: desktopHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body || {}),
  });
  return readStream(response, onEvent);
}

export async function subscribe(path, onEvent) {
  const response = await fetch(path, {
    headers: desktopHeaders({ Accept: "text/event-stream" }),
  });
  return readStream(response, onEvent);
}
