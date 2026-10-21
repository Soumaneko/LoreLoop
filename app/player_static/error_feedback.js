const PROVIDER_MESSAGES = {
  auth_failed: {
    title: "API Key 无效",
    message: "API Key 无效或没有访问权限。",
    guidance: "请前往【设置与检查】更新密钥。",
  },
  permission_denied: {
    title: "模型访问受限",
    message: "当前账号无权访问该模型。",
    guidance: "请前往【设置与检查】检查配置或更换模型。",
  },
  model_not_found: {
    title: "模型不存在",
    message: "指定模型不存在。",
    guidance: "请前往【设置与检查】核对模型名称。",
  },
  thinking_unsupported: {
    title: "模型不支持 Thinking",
    message: "当前模型不支持思考链。",
    guidance: "请前往【设置与检查】关闭 Thinking。",
  },
  rate_limited: {
    title: "请求过于频繁",
    message: "触发服务商频率限制。",
    guidance: "请稍后重试，或检查服务配额。",
  },
  endpoint_unreachable: {
    title: "无法连接服务",
    message: "无法连接 Base URL，请检查地址和网络。",
    guidance: "如使用代理或本地模型服务，也请核对相应配置。",
  },
  provider_error: {
    title: "模型服务异常",
    message: null,
  },
};

export function playerErrorFeedback(
  error,
  fallback = "操作未完成，请查看原因并核实当前状态后再继续。",
  { pendingPreserved = false, confirmationOutcome = null } = {},
) {
  const code = error?.code;
  const preset = PROVIDER_MESSAGES[code];
  let providerMessage;
  if (preset) {
    const cause = error?.message || preset.message || fallback;
    providerMessage = preset.guidance && !cause.includes(preset.guidance)
      ? `${cause}\n${preset.guidance}` : cause;
  }
  if (confirmationOutcome) {
    const statuses = {
      confirmed: ["回合已确认，后续处理未完成", "正式故事已写入；请刷新界面重读最新状态。"],
      not_confirmed: ["本次确认未完成", "本次没有完成确认：可尝试重试，或重新判定。"],
      in_progress: ["确认或恢复仍在进行", "结果尚未确定：暂勿进行操作。"],
      unknown: ["确认状态待核实", "确认结果待核实：请点击【核实确认结果】，暂勿重复确认。"],
    };
    const [title, status] = statuses[confirmationOutcome] || statuses.unknown;
    return { title, message: `${providerMessage || error?.message || "操作未正常结束。"}\n${status}` };
  }

  if (code === "validation_error") {
    return {
      title: "这次操作没有完成",
      message: error?.message || "请求没有完成，请检查后重试。",
    };
  }
  if (code === "timeout") {
    const base = error?.message || "模型请求超时，已停止等待，请稍后重试。";
    if (pendingPreserved) {
      return {
        title: "模型请求超时，待确认内容仍在",
        message: `${base}待确认正文仍然保留在界面中。`,
      };
    }
    return {
      title: "模型请求超时",
      message: base,
    };
  }
  if (preset) {
    return {
      title: preset.title,
      message: providerMessage,
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
      message: `${error?.message || fallback}\n待确认正文仍然保留在界面中。`,
    };
  }
  return {
    title: "这次操作没有完成",
    message: error?.message || fallback,
  };
}
