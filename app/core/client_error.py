class ClientValidationError(ValueError):
    """A deliberate client-input or business-validation error.

    Its public message describes a deliberate refusal. Runtime failures are
    classified separately and linked to a local diagnostic record.
    """

    @property
    def public_message(self) -> str:
        return str(self) or "请求参数不合法。"


def describe_error(exc: BaseException) -> tuple[int, str, str]:
    """Shared desktop feedback for HTTP and background operations."""
    import sqlite3
    from app.core.llm.errors import LLMOutputParseError, LLMOutputValidationError, LLMProviderError
    from app.core.llm.provider_classification import classify_provider_error

    if isinstance(exc, ClientValidationError):
        return 400, "validation_error", exc.public_message
    if isinstance(exc, LLMProviderError):
        code, message = classify_provider_error(exc)
        return 500, code, message
    if isinstance(exc, (LLMOutputParseError, LLMOutputValidationError)):
        return 500, "model_output_invalid", "模型返回内容未通过校验：请重试或更换模型。"
    if isinstance(exc, PermissionError):
        return 500, "file_access_failed", "本地文件访问被拒绝：请检查目录权限和文件占用。如无法解决请点击【复制错误信息】联系开发者。"
    if isinstance(exc, OSError):
        return 500, "file_access_failed", "本地文件操作失败：请检查磁盘空间及访问权限。如无法解决请点击【复制错误信息】联系开发者。"
    if isinstance(exc, sqlite3.Error):
        return 500, "storage_failed", "存档数据库操作失败：请点击【复制错误信息】联系开发者反馈。"
    return 500, "internal_error", "本地操作未能完成，请复制诊断信息以便排查。"
