export function playerErrorFeedback(
  error,
  fallback = "之前的故事没有受到影响，你可以重新尝试。",
  { pendingPreserved = false } = {},
) {
  const code = error?.code;
  if (code === "validation_error") {
    return {
      title: "这次操作没有完成",
      message: error?.message || "请求没有完成，请检查后重试。",
    };
  }
  if (code === "connection_failed" || code === "request_timeout") {
    return {
      title: "连接出现问题",
      message: error?.message || "请检查本地服务或模型连接后重试。",
    };
  }
  if (pendingPreserved) {
    return {
      title: "操作未完成，待确认内容仍在",
      message: "本次操作没有写入正式故事，待确认正文仍然保留在界面中。",
    };
  }
  return {
    title: "这次回应没有完成",
    message: error?.message || fallback,
  };
}
