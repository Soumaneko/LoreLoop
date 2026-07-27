from __future__ import annotations

import sys
from typing import Any


WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


class WebView2RuntimeMissingError(RuntimeError):
    pass


def webview2_runtime_version(
    *,
    platform: str | None = None,
    registry: Any | None = None,
) -> str | None:
    current_platform = sys.platform if platform is None else platform
    if current_platform != "win32":
        return None
    if registry is None:
        import winreg as registry

    paths = (
        rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}",
        rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}",
    )
    for hive_name in ("HKEY_CURRENT_USER", "HKEY_LOCAL_MACHINE"):
        hive = getattr(registry, hive_name)
        for path in paths:
            try:
                with registry.OpenKey(hive, path) as key:
                    version = str(registry.QueryValueEx(key, "pv")[0]).strip()
            except OSError:
                continue
            if version and version != "0.0.0.0":
                return version
    return None


def require_webview2_runtime(
    *,
    platform: str | None = None,
    registry: Any | None = None,
) -> str | None:
    current_platform = sys.platform if platform is None else platform
    if current_platform != "win32":
        return None
    version = webview2_runtime_version(
        platform=current_platform,
        registry=registry,
    )
    if version is None:
        raise WebView2RuntimeMissingError(
            "续章需要 Microsoft Edge WebView2 Runtime。\n\n"
            "请先运行随测试包附带的 MicrosoftEdgeWebview2Setup.exe，"
            "或从微软官网下载后安装：\n"
            f"{WEBVIEW2_DOWNLOAD_URL}"
        )
    return version
