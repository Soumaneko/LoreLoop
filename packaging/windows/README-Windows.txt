续章 · LoreLoop Windows 测试包

1. 请完整解压 ZIP，不要只复制 LoreLoop.exe。
2. 双击 LoreLoop.exe 启动。
3. 测试机不需要安装 Python、Node.js 或项目依赖。
4. 如果程序提示缺少 Microsoft Edge WebView2 Runtime：
   - 运行同目录的 Install-WebView2.cmd；或
   - 前往 https://developer.microsoft.com/microsoft-edge/webview2/ 安装。
5. 存档、模型配置和日志位于：
   %LOCALAPPDATA%\LoreLoop
   API Key 使用当前 Windows 用户的 DPAPI 加密，不能通过复制 model_config.json 迁移到其他用户或电脑。
6. 反馈问题时请附上：
   %LOCALAPPDATA%\LoreLoop\logs\desktop-crash.log
   同时附上程序目录中的 build_info.json。

本测试包未签名，Windows SmartScreen 可能显示未知发布者提示。
如果旧测试包报告 Python.Runtime.Loader.Initialize 错误：
请删除已解压目录，右键最外层下载的 ZIP，选择“属性”→“解除锁定/Unblock”→“应用”，然后重新解压。
