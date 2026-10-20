# Windows 包的补充许可文本

这里按包名保留安装包缺失的许可原文，以及已确认来源的附带组件声明。
构建时只复制实际参与打包的组件所对应的目录，不联网下载，也不做版本或哈希校验。

来源：

- `langchain-core/LICENSE`：[上游 MIT 许可](https://github.com/langchain-ai/langchain/blob/eb705ca4e820450b912ca88c86f36b94b2e6e310/LICENSE)。
- `langsmith/LICENSE`：[上游 MIT 许可](https://github.com/langchain-ai/langsmith-sdk/blob/caf1cccaddd2a8ceeff8e91b821f7a4a432ba5f5/LICENSE)。
- `logfire-api/LICENSE`：[上游 MIT 许可](https://github.com/pydantic/logfire/blob/0decc37da89aeeba2cc7e6a953d18a336fc0b6c4/LICENSE)。
- `proxy-tools/`：[上游 BSD 许可](https://github.com/jtushman/proxy_tools/blob/ccd35a569b95bec271a59890780c7821756c548a/LICENSE.txt)，以及其代码来源 [Werkzeug 的许可和作者声明](https://github.com/pallets/werkzeug/tree/0.9.4)。发布元数据写 MIT，源码声明为 BSD，因此保留原始声明，不根据元数据重写许可。
- `pywebview/WebView2-1.0.3856.49/`：[微软 SDK NuGet 包](https://www.nuget.org/packages/Microsoft.Web.WebView2/1.0.3856.49)中的 LICENSE 和 NOTICE；已与 pywebview 6.2.1 附带的 Core、WinForms 和 x64 Loader DLL 核对。
- `cryptography/OpenSSL-4.0.2/`：[OpenSSL 许可](https://github.com/openssl/openssl/blob/openssl-4.0.2/LICENSE.txt)；该版本来自 cryptography 50.0.1 Windows wheel 中的版本字符串。

发布包的 `THIRD_PARTY_NOTICES.txt` 列出实际安装版本、许可文件位置和对应版本的
源码下载页面；`licenses/` 保存原始文本，包括构建解释器的 Python 许可和
models.dev 许可。未找到依赖许可的情况会在构建日志中提醒。
