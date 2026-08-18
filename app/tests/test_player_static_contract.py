import pytest

from pathlib import Path

from pathlib import Path

STATIC_DIR = Path(__file__).parents[1] / "player_static"


def test_client_diagnostics_uses_desktop_session_token():
    source = (STATIC_DIR / "client_diagnostics.js").read_text(encoding="utf-8")
    assert 'import { desktopHeaders } from "./api.js"' in source
    assert 'headers: desktopHeaders({ "Content-Type": "application/json" })' in source


def test_player_themes_use_semantic_tokens_without_component_overrides():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    preferences_source = (
        STATIC_DIR / "reader_preferences.js"
    ).read_text(encoding="utf-8")
    prepaint_source = (STATIC_DIR / "prepaint.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert styles.count('html[data-theme="') == 2
    assert "ULTIMATE THEME OVERRIDES" not in styles
    assert 'html[data-theme="light"] .' not in styles
    assert 'html[data-theme="sepia"] .' not in styles
    assert "select { color-scheme: dark; }" not in styles
    assert styles.count("/* Rewrite Dropdown Menu Component */") == 1
    for token in (
        "--surface-raised:",
        "--editor-bg:",
        "--positive-text:",
        "--warning-text:",
        "--negative-text:",
            "--highlight-player-text:",
            "--highlight-npc-text:",
            "--highlight-setting-text:",
            "--backdrop:",
    ):
        assert styles.count(token) == 3

    assert 'const CACHE_KEY = "loreloop:reader-preferences"' in preferences_source
    assert "let saveQueue = Promise.resolve()" in preferences_source
    assert "themeColorMeta.content = themeColor" in preferences_source
    assert 'request("/api/player/reader-preferences")' in preferences_source
    assert "one_click_confirm" in preferences_source
    assert "oneClickConfirm: els.oneClickConfirm" in source
    assert "autoConfirmAfterRecovery" in source
    assert "shouldAutoConfirmTurn" in (STATIC_DIR / "state.js").read_text(encoding="utf-8")
    assert "maybeAutoConfirm" in source
    assert 'id="oneClickConfirm"' in html
    assert "一键确认" in html
    assert "成本与质量" in html
    assert 'id="canonInfoBtn"' in html
    assert 'id="tokenUsageInfoBtn"' in html
    assert 'id="oneClickInfoBtn"' in html
    assert 'id="settingInfoDialog"' in html
    assert 'from "./setting_info.js"' in source
    assert "bindSettingInfoButtons" in source
    assert "bindSettingInfoDismiss" in source
    assert "setting-inline-note" in html
    assert "setting-inline-note" in styles
    assert "校验正文与已确定事实的一致性" in html
    assert "显示 Token 消耗" in html
    assert "正文生成后自动确认" in html
    assert "可能产生与已确定事实冲突的内容" in source
    assert "不影响生成质量或实际成本" in source
    assert "增加 Token 消耗" in source
    assert "LLM 随机性导致正文质量波动" in source
    assert ".token-usage-popover:not([open])" in styles
    assert "@supports (selector(:popover-open))" in styles
    assert 'localStorage.getItem(cacheKey)' in prepaint_source
    assert 'src="/assets/prepaint.js"' in html
    assert 'meta name="theme-color" content="#11110e"' in html


def test_player_error_card_uses_frontend_safe_feedback():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    feedback_source = (STATIC_DIR / "error_feedback.js").read_text(encoding="utf-8")

    assert 'id="errorTitle"' in html
    assert 'from "./error_feedback.js"' in source
    assert "pendingPreserved" in source
    assert "playerErrorFeedback(error, fallback, options)" in source
    assert '"操作未完成，待确认内容仍在"' in feedback_source
    assert '"本次操作没有写入正式故事，待确认正文仍然保留在界面中。"' in feedback_source
    assert '"这次操作没有完成"' in feedback_source
    assert '"输入有误"' not in feedback_source
    assert '"连接出现问题"' in feedback_source


def test_player_frontend_uses_only_player_api_and_hides_debug_controls():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "<title>续章 · 你的故事</title>" in html
    assert "<strong>续章</strong>" in html
    assert "<small>LORELOOP</small>" in html
    assert "/api/player/state" in source
    assert 'await request("/api/player/model-config")' in source
    assert 'request("/api/player/model-config/test"' in source
    assert 'request("/api/player/model-config"' in source
    assert 'const saves = await request("/api/player/saves")' in source
    assert 'await request("/api/player/state")' in source
    assert 'id="canonValidationBtn"' in html
    assert 'id="closeInitDialogBtn"' in html
    assert 'id="continueInitBtn"' in html
    assert 'request("/api/player/init/draft")' in source
    assert 'stream("/api/player/opening/rewrite"' in source
    assert '"重新生成开场"' in source
    assert "pending.kind === \"opening\"" in source
    assert 'request("/api/player/canon-validation"' in source
    assert 'initDirectorEnabled' not in html and "director_enabled" not in source
    assert 'request("/api/player/experience-mode"' not in source
    assert 'request("/api/player/experience-mode"' in (
        STATIC_DIR / "api.js"
    ).read_text(encoding="utf-8")
    assert "switchExperienceMode" in source
    assert "experienceModePlayerBtn" in html and "experienceModeCreatorBtn" in html
    assert "dataset.uiMode" in source
    assert 'recordsTabEvent' in html and "renderEventRecords" in source
    assert 'id="coreMemoryDialog"' in html and "openCoreMemoryEditor" in source
    assert 'data-creator-only' in html
    assert 'version: 4,' in source
    assert 'state.playerState?.initialization?.phase !== "config"' in source
    assert "Promise.all([" not in source
    assert "/api/player/history?before_turn=" in source
    assert 'id="turnNavigator"' in html
    assert 'id="turnNavigatorList"' in html
    assert 'aria-label="回合导航"' in html
    assert "function renderTurnNavigator()" in source
    assert "function jumpToHistoryTurn(turnIndex)" in source
    assert "new IntersectionObserver(" in source
    assert "function refreshNavigationObserver(" in source
    assert "function setNavigationActive(key)" in source
    assert "scheduleNavigationActiveUpdate" not in source
    assert "HISTORY_NAVIGATION_LOAD_SIZE = 20" in source
    assert 'next.setAttribute("aria-current", "step")' in source
    assert 'if ("onscrollend" in window)' in source
    assert "navigationActiveKey === nextKey" in source
    assert "return insertedCount;" in source
    assert ".turn-navigator-list::before" in styles
    assert ".turn-nav-item.active .turn-nav-dot" in styles
    assert "/api/player/generate" in source
    assert "/api/player/${endpoint}" in source
    assert 'retryTurn("rephrase")' in source
    assert 'retryTurn("reroll")' in source
    assert "/api/player/feedback-rewrite" in source
    assert 'id="feedbackRewriteDialog"' in html
    assert '"根据意见修改"' in source
    assert '"重新措辞"' in source
    assert '"重新判定"' in source
    assert "/api/player/confirm" in source
    assert "/api/player/operations/recoverable" in source
    assert "/api/player/operations/${encodeURIComponent(operationId)}/ack" in (
        STATIC_DIR / "api.js"
    ).read_text(encoding="utf-8")
    assert "/api/player/operations/${encodeURIComponent(operation.operation_id)}/events" in source
    assert "request_id: newRequestId()" in source
    assert "function resumeConfirmationOperation(" in source
    assert 'GENERATION_IN_FLIGHT_KEY = "loreloop:generation-in-flight"' in source
    assert 'CONFIRMATION_IN_FLIGHT_KEY = "loreloop:confirmation-in-flight"' in source
    assert "function restoreGenerationRunPlaceholder(" in source
    assert "activeGenerationOperation" in source
    assert 'if (entry.status === "generating") return `${entry.label} · 正在生成`;' in source
    assert "acknowledgeOperation" in source
    assert 'from "./api.js"' in source
    assert "/api/player/init/campaign-setup" in source
    assert 'operation.recovery_kind === "initialization"' in source
    assert "for (const operation of recoverableOperations)" in source
    assert "function resumeInitializationOperation(" in source
    assert "campaign_setup_done" in source
    assert "/api/state" not in source
    assert "/api/traces" not in source
    assert "/api/maintenance-jobs" not in source
    assert 'href="/debug"' not in html
    assert 'id="welcome" class="welcome-card" hidden' in html
    assert "maxCharsInput" not in html
    assert "writeDbToggle" not in html
    assert "loadEarlierHistory" in source
    assert "addedHeight" in source
    assert "appendHighlightedText" in source
    assert "openCodexProfile" in source
    assert "preview.good_result" in source
    assert "renderPlayerResolution" in source
    assert "function hasVisibleRoll(action)" in source
    assert "resolution-auto-detail" not in source
    assert "系统自动判定（无需骰点）" not in source
    assert 'if (mode === "outcome")' in source
    assert '"resolution-outcome resolution-outcome-line"' in source
    assert "`${item.label} ${valueText}`" in source
    assert "`${item.label} ${valueText}：${item.reason}`" not in source
    assert "`[${values.join(\", \")}] → ${keptValue}`" in source
    assert "`最终值 ${finalValue}/${sides}　${outcome}`" in source
    assert "修正明细未记录" in source
    assert "confirmDiscardUnsavedPendingEdit" in source
    assert "切换存档会丢失尚未保存的修改" in source
    assert "restoreOriginalScene" in source
    assert 'request("/api/player/pending/edit"' in source
    assert '"恢复原文"' in source
    assert "已恢复该版本的原始正文" in source
    assert "discard_pending" not in source
    assert "stopActiveGeneration" in source
    assert "operation_cancelled" in source
    assert "已停止生成" in source
    assert 'id="stopGenerationBtn"' in html
    assert "/cancel" in (STATIC_DIR / "api.js").read_text(encoding="utf-8")
    assert "/api/player/dice-display" in source
    assert 'id="sceneSummary"' in html
    assert 'id="codexDialog"' in html
    assert 'id="diceDisplaySelect"' in html
    assert "innerHTML" not in source
    assert "appendStreamingText" in source
    assert "followStream" not in source
    assert "isNearBottom" not in source
    assert 'id="tokenUsageSelect"' in html
    assert 'id="tokenUsageDialog"' in html
    assert "createTokenUsageController" in source
    assert "tokenUsage.handleEvent(event)" in source
    assert "bindPopoverFallbackDismiss" in source
    assert "openTokenUsagePipelineDialog" in (
        STATIC_DIR / "token_usage.js"
    ).read_text(encoding="utf-8")
    assert 'else dialog.setAttribute("open", "")' in (
        STATIC_DIR / "token_usage.js"
    ).read_text(encoding="utf-8")
    assert "show_token_usage" in (
        STATIC_DIR / "reader_preferences.js"
    ).read_text(encoding="utf-8")
    assert 'event.step === "done"' in (
        STATIC_DIR / "api.js"
    ).read_text(encoding="utf-8")
    assert "narrative_check_started" in source
    assert "canon_query_started" in source
    assert "narrative_revision_started" in source
    assert "event.data?.revision" in source
    assert 'loadAll({ scroll: "instant" })' in source
    assert ".composer-shell:focus-within" in styles
    assert ".story-highlight-npc" in styles
    assert ".story-highlight-setting" in styles
    assert "inventory-quantity" in source
    assert ".resource-group-inventory .resource-items" in styles
    assert "width: min(1040px" in styles
    assert "backdrop-filter" not in styles
    assert 'class="ambient ' not in html
    assert ".ambient {" not in styles
    assert ".narrative-card::before" not in styles
    assert "transform: translateZ(0)" not in styles
    assert "0 0 13px var(--gold-glow)" not in styles
    assert "0 0 12px var(--gold-glow)" not in styles
    assert ".init-modal [hidden]" in styles
    assert "select option, select optgroup" in styles
    assert 'id="activePlayerAction"' in html
    assert "showActivePlayerAction(userInput)" in source
    assert '正在解析：尝试' in source
    assert '检定已掷出：' in source
    assert '机制判定达成：' in source
    assert 'const PLAYER_SHELL_CACHE_KEY = "loreloop:player-shell"' in source
    assert "function restorePlayerShell()" in source
    assert 'sessionStorage.setItem(CONFIRMATION_IN_FLIGHT_KEY, "1")' in source
    assert "restorePlayerShell();" in source
    assert 'confirm.textContent = confirming ? "正在确认……"' in source
    assert "_handle_player_json_stream" not in (
        Path(__file__).parents[1] / "debug_server.py"
    ).read_text(encoding="utf-8")
    assert "playerState.action_preview?.previews" in source
    assert "fanfic: false" not in source
    assert "version: 4" in source
    for field in (
        'name="world_name"',
        'name="world_style"',
        'name="ability_system"',
        'name="custom_ability_system"',
        'name="protagonist_age"',
        'name="protagonist_introduction"',
        'name="story_expectation"',
        'name="failure_severity"',
        "abilityRows",
        "addAbilityBtn",
        "setupCurrencyUnit",
        "setupPowerSource",
        "setupPowerPrevalence",
        "setupPeoplesAndBeings",
        "setupMajorWorldConflicts",
        "setupDominantPowers",
        "setupCivilizationBaseline",
        "setupPowerLearningDifficulty",
        "setupPowerLimitation",
    ):
        assert field in html
    assert "campaign_setup" in source
    assert 'name="work_name"' not in html
    assert "extraCoreMemoryList" not in html
    assert 'name="narrative_tone"' not in html
    assert "initConfigSnapshot" in source
    assert "function setCampaignSetupControlsDisabled(disabled)" in source
    assert '"input, textarea, select"' in source
    assert "if (!regenerate) await persistInitializationDraft();" in source
    assert 'throw new Error("自定义世界风格不能为空")' in source
    assert "draft.campaign_setup?.version === 2" in source
    assert ".save-manager-intro" not in styles
    assert 'id="manageSavesBtn"' in html
    assert 'id="modelSettingsBtn"' in html
    assert 'id="modelConfigDialog"' in html
    assert 'id="modelApiKey" type="password"' in html
    assert "modelApiKey.value = config" not in source
    assert 'els.modelApiKey.value = ""' in source
    assert 'setMode("needs_model")' in source
    assert "enterModelSetup(modelConfig)" in source
    assert 'id="saveManagerDialog"' in html
    assert "save.last_played_at" in source
    assert "save.turn_count" in source
    assert "save.protagonist_name" in source
    assert "save.location" in source
    assert 'option.value = save.save_id' in source
    assert '/api/player/saves/rename' in source
    assert '/api/player/saves/copy' in source
    assert '/api/player/saves/delete' in source
    api_source = (STATIC_DIR / "api.js").read_text(encoding="utf-8")
    assert '/api/player/saves/export?save_id=' in api_source
    assert '/api/player/saves/import?name=' in api_source
    assert '"Content-Type": "application/zip"' in api_source
