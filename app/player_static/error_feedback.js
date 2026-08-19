const PROVIDER_MESSAGES = {
  auth_failed: {
    title: "API Key 无效",
    message: "API Key 无效或没有访问权限，请前往模型设置更新后重试。",
  },
  permission_denied: {
    title: "模型访问受限",
    message: "API Key 有效，但没有访问当前服务或模型的权限，请检查模型配置。",
  },
  model_not_found: {
    title: "模型不存在",
    message: "服务可访问，但没有找到指定模型，请检查模型名称。",
  },
  thinking_unsupported: {
    title: "模型不支持 Thinking",
    message: "当前服务或模型不支持所选 Thinking 设置，请关闭后重试。",
  },
  rate_limited: {
    title: "请求过于频繁",
    message: "服务当前触发了速率限制，请稍后重试或检查配额。",
  },
  timeout: {
    title: "连接超时",
    message: "连接模型服务超时，请检查网络后重试。",
  },
  endpoint_unreachable: {
    title: "无法连接服务",
    message: "无法连接 Base URL，请检查地址和网络。",
  },
  provider_error: {
    title: "模型服务异常",
    message: null,
  },
};

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
  if (PROVIDER_MESSAGES[code]) {
    const preset = PROVIDER_MESSAGES[code];
    return {
      title: preset.title,
      message: error?.message || preset.message || fallback,
      action: "openModelConfig",
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
