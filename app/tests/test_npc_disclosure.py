from app.domain.npc_profile.schemas import (
    DISCLOSED_FIELD_SETS,
    NPCProfile,
)


def _profile():
    return NPCProfile(
        name="林澈",
        aliases=["林医生"],
        role="社区医生",
        public_summary="诊所的坐堂医生。",
        appearance="常穿深色风衣。",
        personality_traits=["谨慎", "耐心"],
        known_abilities=["急救"],
        faction=None,
        attitude_to_player="克制冷淡。",
        social_style="克制而友善。",
        core_motivation="保护社区居民。",
        hidden_secret="曾目击一宗未结案件。",
        conflict_style="先协商后行动。",
        director_notes="导演笔记。",
    )


def test_default_disclosure_level_is_remote():
    assert _profile().disclosure_level == "remote"


def test_disclosure_view_remote_drops_face_to_face_and_hidden_fields():
    view = _profile().disclosure_view()
    data = view.model_dump(mode="json")

    assert data["name"] == "林澈"
    assert data["role"] == "社区医生"
    assert data["social_style"] == "克制而友善。"
    assert data["attitude_to_player"] == "克制冷淡。"
    assert data["appearance"] is None
    assert data["personality_traits"] == []
    assert data["known_abilities"] == []
    assert data["core_motivation"] is None
    assert data["hidden_secret"] is None


def test_disclosure_view_face_to_face_adds_appearance_and_personality():
    data = _profile().disclosure_view("face_to_face").model_dump(mode="json")

    assert data["appearance"] == "常穿深色风衣。"
    assert data["personality_traits"] == ["谨慎", "耐心"]
    assert data["known_abilities"] == ["急救"]
    assert data["conflict_style"] == "先协商后行动。"
    assert data["core_motivation"] is None
    assert data["hidden_secret"] is None


def test_disclosure_view_intimate_reveals_everything():
    data = _profile().disclosure_view("intimate").model_dump(mode="json")

    assert data["core_motivation"] == "保护社区居民。"
    assert data["hidden_secret"] == "曾目击一宗未结案件。"


def test_disclosure_view_uses_profile_level_when_not_specified():
    profile = _profile().model_copy(update={"disclosure_level": "intimate"})
    data = profile.disclosure_view().model_dump(mode="json")
    assert data["hidden_secret"] == "曾目击一宗未结案件。"


def test_director_view_includes_current_disclosure_level():
    profile = _profile().model_copy(
        update={"disclosure_level": "face_to_face"}
    )
    data = profile.director_view().model_dump(mode="json")
    assert data["disclosure_level"] == "face_to_face"


def test_disclosure_view_never_leaks_disclosure_level():
    for level in ("remote", "face_to_face", "intimate"):
        data = _profile().disclosure_view(level).model_dump(mode="json")
        assert data.get("disclosure_level") is None
    intimate = _profile().disclosure_view("intimate").model_dump(mode="json")
    assert intimate["hidden_secret"] == "曾目击一宗未结案件。"


def test_disclosed_field_sets_cover_all_levels():
    assert DISCLOSED_FIELD_SETS["remote"] is not None
    assert DISCLOSED_FIELD_SETS["face_to_face"] is not None
    assert DISCLOSED_FIELD_SETS["intimate"] is None
    assert DISCLOSED_FIELD_SETS["remote"] < DISCLOSED_FIELD_SETS["face_to_face"]
