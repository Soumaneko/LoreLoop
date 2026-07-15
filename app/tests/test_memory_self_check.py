import pytest

from app.domain.recall_memory.schemas import Memory
from app.domain.recall_memory.self_check import MemorySelfCheck, MAX_TAGS
from app.domain.current_state import CurrentState, SceneState, PlayerState, ResourcesState, WorldClock

def _cs(scene_id="test", location="", time_label="", participants=None, viewpoint="主角"):
    world_clock = WorldClock(day=1, phase="上午")
    if "第2日" in time_label:
        world_clock = WorldClock(day=2, phase="晚上" if "夜" in time_label else "上午")
    return CurrentState(
        scene=SceneState(scene_id=scene_id, location=location, participants=participants or []),
        world_clock=world_clock,
        player=PlayerState(name=viewpoint),
        resources=ResourcesState(),
    )


SCENE_TEXT = "在灰鹭旅店二楼，主角告诉伊莱娜，如果他用左手轻敲两下桌面，就表示让她保持沉默。伊莱娜点头答应。"
SCENE_CONTEXT = _cs(scene_id="scene_002", location="灰鹭旅店二楼", time_label="第2日夜晚", viewpoint="主角", participants=["主角", "伊莱娜"])


def _make_memory(**overrides) -> Memory:
    defaults = {
        "memory_kind": "commitment", "evidence_mode": "observed",
        "subject": "沉默暗号",
        "content": "主角和伊莱娜约定：左手轻敲两下桌面表示保持沉默。",
        "entities": ["主角", "伊莱娜"],
        "location": "灰鹭旅店二楼",
        "time_label": "第2日夜晚",
        "visibility": ["主角", "伊莱娜"],
        "tags": ["暗号", "沉默"],
        "confidence": 1.0,
        "salience": 0.8,
        "source_scene_id": "scene_002",
        "source_text_span": ["用左手轻敲两下桌面，就表示让她保持沉默"],
    }
    defaults.update(overrides)
    return Memory(**defaults)


SC = MemorySelfCheck()


class TestSourceTextSpan:
    def test_valid_span_passes(self):
        m = _make_memory(source_text_span=["用左手轻敲两下桌面，就表示让她保持沉默"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1
        assert result.warning_count == 0

    def test_empty_span_warns(self):
        m = _make_memory(source_text_span=[])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1
        assert result.warning_count >= 1
        assert any("empty" in w for w in result.warnings)

    def test_ellipsis_warns(self):
        m = _make_memory(source_text_span=["用左手...保持沉默"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1
        assert any("..." in w for w in result.warnings)

    def test_unicode_ellipsis_warns(self):
        m = _make_memory(source_text_span=["用左手…保持沉默"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1
        assert any("…" in w for w in result.warnings)

    def test_not_substring_warns(self):
        m = _make_memory(source_text_span=["这是改写过的文本而非原文"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1
        assert any("substring" in w for w in result.warnings)

    def test_span_with_extra_whitespace_trimmed(self):
        m = _make_memory(source_text_span=["  用左手轻敲两下桌面，就表示让她保持沉默  "])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1


class TestSceneContextFix:
    def test_source_scene_id_corrected(self):
        m = _make_memory(source_scene_id="wrong_id")
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1
        assert cleaned[0].source_scene_id == "scene_002"
        assert any("source_scene_id" in f for f in result.fixes)

    def test_location_filled_from_context(self):
        m = _make_memory(location=None)
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert cleaned[0].location == "灰鹭旅店二楼"
        assert any("location" in f for f in result.fixes)

    def test_time_label_filled_from_context(self):
        m = _make_memory(time_label=None)
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert cleaned[0].time_label == "第2日晚上"
        assert any("time_label" in f for f in result.fixes)

    def test_pending_source_does_not_get_current_context_location_or_time(self):
        m = _make_memory(
            location=None,
            time_label=None,
            source_pending_ids=["pending-1"],
        )
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert cleaned[0].location is None
        assert cleaned[0].time_label is None
        assert not any("location filled from current_state" in f for f in result.fixes)
        assert not any("time_label filled from current_state" in f for f in result.fixes)

    def test_existing_values_not_overwritten(self):
        m = _make_memory(location="自定地点", time_label="自定时间")
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert cleaned[0].location == "自定地点"
        assert cleaned[0].time_label == "自定时间"


class TestVisibility:
    def test_valid_visibility_passes(self):
        m = _make_memory(visibility=["主角", "伊莱娜"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1

    def test_public_visibility_allowed(self):
        m = _make_memory(visibility=["public"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1

    def test_unknown_role_warns(self):
        m = _make_memory(visibility=["主角", "神秘人"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1
        assert result.warning_count >= 1
        assert any("unknown" in w for w in result.warnings)

    def test_empty_visibility_warns(self):
        m = _make_memory(visibility=[])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1
        assert result.warning_count >= 1
        assert any("empty" in w for w in result.warnings)


class TestEntities:
    def test_valid_entities_pass(self):
        m = _make_memory(entities=["主角", "伊莱娜"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1
        assert set(cleaned[0].entities) == {"主角", "伊莱娜"}

    def test_duplicate_entities_removed(self):
        m = _make_memory(entities=["主角", "伊莱娜", "主角", "主角"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert cleaned[0].entities == ["主角", "伊莱娜"]

    def test_entity_not_in_text_warns_only(self):
        m = _make_memory(entities=["主角", "守卫队长"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1
        assert "守卫队长" in cleaned[0].entities
        assert "主角" in cleaned[0].entities
        assert any("守卫队长" in w for w in result.warnings)

    def test_entity_not_in_context_warns_only(self):
        m = _make_memory(entities=["viewpoint"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1
        assert "viewpoint" in cleaned[0].entities
        assert any("viewpoint" in w for w in result.warnings)

    def test_empty_entity_string_removed(self):
        m = _make_memory(entities=["主角", "", "   ", "伊莱娜"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert cleaned[0].entities == ["主角", "伊莱娜"]


class TestTags:
    def test_valid_tags_pass(self):
        m = _make_memory(tags=["暗号", "沉默", "约定"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1
        assert set(cleaned[0].tags) == {"暗号", "沉默", "约定"}

    def test_duplicate_tags_removed(self):
        m = _make_memory(tags=["暗号", "暗号", "沉默", "暗号"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert cleaned[0].tags == ["暗号", "沉默"]

    def test_empty_tags_removed(self):
        m = _make_memory(tags=["暗号", "", "   ", "沉默"])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert cleaned[0].tags == ["暗号", "沉默"]

    def test_tags_capped_at_max(self):
        m = _make_memory(tags=[f"tag_{i}" for i in range(20)])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned[0].tags) == MAX_TAGS


class TestDeduplication:
    def test_exact_duplicate_within_batch_removed(self):
        m1 = _make_memory(subject="记忆A", content="内容A", entities=["主角"])
        m2 = _make_memory(subject="记忆A", content="内容A", entities=["主角"])
        cleaned, result = SC.check([m1, m2], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 1
        assert any("Duplicate" in f for f in result.fixes)

    def test_different_subject_not_deduped(self):
        m1 = _make_memory(subject="记忆A", content="同内容", entities=["主角"])
        m2 = _make_memory(subject="记忆B", content="同内容", entities=["主角"])
        cleaned, result = SC.check([m1, m2], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 2

    def test_different_content_not_deduped(self):
        m1 = _make_memory(subject="同主题", content="内容A", entities=["主角"])
        m2 = _make_memory(subject="同主题", content="内容B", entities=["主角"])
        cleaned, result = SC.check([m1, m2], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 2

    def test_different_entities_not_deduped(self):
        m1 = _make_memory(subject="同主题", content="同内容", entities=["主角"])
        m2 = _make_memory(subject="同主题", content="同内容", entities=["主角", "伊莱娜"])
        cleaned, result = SC.check([m1, m2], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 2

    def test_different_subtype_not_deduped(self):
        m1 = _make_memory(memory_kind="commitment", evidence_mode="observed", subject="同主题", content="同内容", entities=["主角"])
        m2 = _make_memory(memory_kind="open_thread", evidence_mode="observed", subject="同主题", content="同内容", entities=["主角"])
        cleaned, result = SC.check([m1, m2], SCENE_TEXT, SCENE_CONTEXT)
        assert len(cleaned) == 2


class TestSelfCheckResult:
    def test_result_original_and_final_count(self):
        m1 = _make_memory(subject="有效")
        m2 = _make_memory(subject="无效", source_text_span=["不存在原文"])
        cleaned, result = SC.check([m1, m2], SCENE_TEXT, SCENE_CONTEXT)
        assert result.original_count == 2
        assert result.final_count == 2  # no deletion, both kept
        assert result.warning_count >= 1

    def test_all_passing_is_ok(self):
        m = _make_memory()
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert result.ok
        assert result.warning_count == 0

    def test_warnings_dont_break_ok(self):
        m = _make_memory(visibility=[])
        cleaned, result = SC.check([m], SCENE_TEXT, SCENE_CONTEXT)
        assert result.ok  # still ok — warnings don't cause removal
        assert result.warning_count >= 1
        assert result.final_count == 1
