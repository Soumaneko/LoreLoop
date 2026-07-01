import pytest

from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.roll.resolver import RollResolver
from app.domain.roll.schemas import RollConfig
from app.domain.roll_modifier.schemas import RollModifier, RollModifierBatch


def _make_action(
    action_id: str,
    sequence: int,
    likely_needs_roll: bool,
    roll_hint: str | None = None,
) -> AtomicAction:
    return AtomicAction(
        action_id=action_id,
        sequence=sequence,
        actor="主角",
        action_type="observe",
        raw_text="观察四周",
        verb="观察",
        target="四周",
        method=None,
        goal=None,
        condition=None,
        likely_needs_roll=likely_needs_roll,
        roll_hint=roll_hint,
        confidence=0.9,
    )


class TestDefaultRoll:
    def test_default_roll_is_d20(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[
                _make_action("a1", 1, True, "perception"),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(min_rolls=0))
        result = resolver.resolve(parse_result)

        assert result.dice_formula == "1d20"
        assert result.dice_sides == 20
        assert result.total_rolls == 1
        assert result.results[0].dice == "1d20"
        assert 1 <= result.results[0].value <= 20

    def test_custom_dice_sides(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[
                _make_action("a1", 1, True),
                _make_action("a2", 2, True),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(dice_sides=6, min_rolls=0))
        result = resolver.resolve(parse_result)

        assert result.dice_formula == "2d6"
        assert result.dice_sides == 6
        assert result.total_rolls == 2

        for roll in result.results:
            assert roll.dice == "1d6"
            assert 1 <= roll.value <= 6

    def test_roll_only_when_needed(self):
        parse_result = ActionParseResult(
            turn_intent="潜入并等待",
            atomic_actions=[
                _make_action("a1", 1, True, "stealth"),
                _make_action("a2", 2, False, None),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(roll_only_when_needed=True, min_rolls=0))
        result = resolver.resolve(parse_result)

        assert result.dice_formula == "1d20"
        assert result.total_rolls == 1
        assert result.results[0].action_id == "a1"

    def test_roll_all_actions_when_disabled(self):
        parse_result = ActionParseResult(
            turn_intent="潜入并等待",
            atomic_actions=[
                _make_action("a1", 1, True),
                _make_action("a2", 2, False),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(roll_only_when_needed=False, min_rolls=0))
        result = resolver.resolve(parse_result)

        assert result.dice_formula == "2d20"
        assert result.total_rolls == 2
        assert [r.action_id for r in result.results] == ["a1", "a2"]

    def test_seeded_rolls_are_reproducible(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[
                _make_action("a1", 1, True),
                _make_action("a2", 2, True),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver_1 = RollResolver(RollConfig(seed=123, min_rolls=0))
        resolver_2 = RollResolver(RollConfig(seed=123, min_rolls=0))

        result_1 = resolver_1.resolve(parse_result)
        result_2 = resolver_2.resolve(parse_result)

        assert [r.value for r in result_1.results] == [
            r.value for r in result_2.results
        ]

    def test_empty_actions_return_zero_rolls(self):
        parse_result = ActionParseResult(
            turn_intent="无行动",
            atomic_actions=[],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(min_rolls=0))
        result = resolver.resolve(parse_result)

        assert result.dice_formula == "0d20"
        assert result.total_rolls == 0
        assert result.results == []

    def test_invalid_dice_sides(self):
        with pytest.raises(Exception):
            RollConfig(dice_sides=1)


class TestResultFields:
    def test_preserves_action_mapping(self):
        parse_result = ActionParseResult(
            turn_intent="潜入并观察",
            atomic_actions=[
                AtomicAction(
                    action_id="a1",
                    sequence=1,
                    actor="主角",
                    action_type="hide_or_stealth",
                    raw_text="躲进阴影",
                    verb="躲藏",
                    target="阴影",
                    method=None,
                    goal="避免被发现",
                    condition=None,
                    likely_needs_roll=True,
                    roll_hint="stealth",
                    confidence=0.92,
                ),
                AtomicAction(
                    action_id="a2",
                    sequence=2,
                    actor="主角",
                    action_type="observe",
                    raw_text="观察守卫动向",
                    verb="观察",
                    target="守卫",
                    method=None,
                    goal="确认巡逻路线",
                    condition=None,
                    likely_needs_roll=True,
                    roll_hint="perception",
                    confidence=0.88,
                ),
                AtomicAction(
                    action_id="a3",
                    sequence=3,
                    actor="主角",
                    action_type="wait",
                    raw_text="等他们走远",
                    verb="等待",
                    target="守卫",
                    method=None,
                    goal="寻找安全时机",
                    condition=None,
                    likely_needs_roll=False,
                    roll_hint=None,
                    confidence=0.84,
                ),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(seed=42, min_rolls=0))
        result = resolver.resolve(parse_result)

        assert result.total_rolls == 2

        a1_result = result.results[0]
        assert a1_result.action_id == "a1"
        assert a1_result.sequence == 1
        assert a1_result.actor == "主角"
        assert a1_result.action_type == "hide_or_stealth"
        assert a1_result.roll_hint is None

        a2_result = result.results[1]
        assert a2_result.action_id == "a2"
        assert a2_result.sequence == 2
        assert a2_result.actor == "主角"
        assert a2_result.action_type == "observe"
        assert a2_result.roll_hint is None

    def test_dice_formula_matches_total_rolls(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[
                _make_action("a1", 1, True),
                _make_action("a2", 2, True),
                _make_action("a3", 3, True),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(dice_sides=100, min_rolls=0))
        result = resolver.resolve(parse_result)

        assert result.dice_formula == "3d100"
        assert result.total_rolls == 3
        assert result.dice_sides == 100

    def test_no_roll_actions_with_custom_dice_sides(self):
        parse_result = ActionParseResult(
            turn_intent="无行动",
            atomic_actions=[
                _make_action("a1", 1, False),
                _make_action("a2", 2, False),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(dice_sides=6, min_rolls=0))
        result = resolver.resolve(parse_result)

        assert result.dice_formula == "0d6"
        assert result.total_rolls == 0


class TestRollModifiers:
    def test_modifier_adjusts_value_and_records_raw_value(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[_make_action("a1", 1, True)],
            anchors=[],
            ambiguities=[],
        )
        modifier_batch = RollModifierBatch(
            modifiers=[
                RollModifier(
                    action_id="a1",
                    label="有利地形",
                    reason="当前位置提供清晰视野。",
                    value=3,
                )
            ]
        )

        resolver = RollResolver(RollConfig(seed=1, min_rolls=0))
        result = resolver.resolve(parse_result, modifier_batch=modifier_batch)

        assert result.results[0].raw_value == 5
        assert result.results[0].value == 8
        assert result.results[0].modifiers == modifier_batch.modifiers

    def test_modifier_clamps_upper_bound(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[_make_action("a1", 1, True)],
            anchors=[],
            ambiguities=[],
        )
        modifier_batch = RollModifierBatch(
            modifiers=[
                RollModifier(
                    action_id="a1",
                    label="强力支援",
                    reason="盟友提供明确支援。",
                    value=5,
                )
            ]
        )

        resolver = RollResolver(RollConfig(seed=1, min_rolls=0))
        result = resolver.resolve(
            parse_result,
            modifier_batch=modifier_batch,
            roll_actions=[
                _make_action("skip", 1, True),
                _make_action("a1", 2, True),
            ],
        )

        assert result.results[1].raw_value == 19
        assert result.results[1].value == 20

    def test_modifier_clamps_lower_bound(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[_make_action("a1", 1, True)],
            anchors=[],
            ambiguities=[],
        )
        modifier_batch = RollModifierBatch(
            modifiers=[
                RollModifier(
                    action_id="a1",
                    label="严重干扰",
                    reason="目标环境明显不利。",
                    value=-5,
                )
            ]
        )

        resolver = RollResolver(RollConfig(seed=2, min_rolls=0))
        result = resolver.resolve(parse_result, modifier_batch=modifier_batch)

        assert result.results[0].raw_value == 2
        assert result.results[0].value == 1

    def test_no_modifier_keeps_existing_value_behavior(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[_make_action("a1", 1, True)],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(seed=1, min_rolls=0))
        result = resolver.resolve(parse_result)

        assert result.results[0].raw_value == 5
        assert result.results[0].value == 5
        assert result.results[0].modifiers == []

    def test_select_roll_actions_can_be_reused_for_modifier_evaluation(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[
                _make_action("a1", 1, True),
                _make_action("a2", 2, False),
                _make_action("a3", 3, False),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(min_rolls=2, seed=42))
        roll_actions = resolver.select_roll_actions(parse_result)
        result = resolver.resolve(parse_result, roll_actions=roll_actions)

        assert [action.action_id for action in roll_actions] == [
            roll.action_id for roll in result.results
        ]


class TestConfigValidation:
    def test_min_dice_sides(self):
        cfg = RollConfig(dice_sides=2)
        assert cfg.dice_sides == 2

    def test_max_dice_sides(self):
        cfg = RollConfig(dice_sides=1000)
        assert cfg.dice_sides == 1000

    def test_dice_sides_too_large(self):
        with pytest.raises(Exception):
            RollConfig(dice_sides=1001)


class TestMinRolls:
    def test_default_is_2(self):
        cfg = RollConfig()
        assert cfg.min_rolls == 2

    def test_sufficient_needs_no_extra(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[
                _make_action("a1", 1, True),
                _make_action("a2", 2, True),
                _make_action("a3", 3, False),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(min_rolls=2))
        result = resolver.resolve(parse_result)

        assert result.total_rolls == 2
        assert {r.action_id for r in result.results} == {"a1", "a2"}

    def test_supplements_from_false_pool(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[
                _make_action("a1", 1, True),
                _make_action("a2", 2, False),
                _make_action("a3", 3, False),
                _make_action("a4", 4, False),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(min_rolls=3, seed=99))
        result = resolver.resolve(parse_result)

        assert result.total_rolls == 3
        ids = {r.action_id for r in result.results}
        assert "a1" in ids

    def test_not_enough_false_caps_at_total(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[
                _make_action("a1", 1, True),
                _make_action("a2", 2, False),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(min_rolls=5))
        result = resolver.resolve(parse_result)

        assert result.total_rolls == 2

    def test_zero_disables_min_rolls(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[
                _make_action("a1", 1, True),
                _make_action("a2", 2, False),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(min_rolls=0))
        result = resolver.resolve(parse_result)

        assert result.dice_formula == "1d20"
        assert result.total_rolls == 1
        assert result.results[0].action_id == "a1"

    def test_all_false_still_rolls_up_to_min(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[
                _make_action("a1", 1, False),
                _make_action("a2", 2, False),
                _make_action("a3", 3, False),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(min_rolls=2, seed=42))
        result = resolver.resolve(parse_result)

        assert result.total_rolls == 2

    def test_seed_covers_forced_selection(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[
                _make_action("a1", 1, True),
                _make_action("a2", 2, False),
                _make_action("a3", 3, False),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver_1 = RollResolver(RollConfig(min_rolls=2, seed=42))
        resolver_2 = RollResolver(RollConfig(min_rolls=2, seed=42))

        result_1 = resolver_1.resolve(parse_result)
        result_2 = resolver_2.resolve(parse_result)

        ids_1 = [r.action_id for r in result_1.results]
        ids_2 = [r.action_id for r in result_2.results]
        assert ids_1 == ids_2
        assert [r.value for r in result_1.results] == [
            r.value for r in result_2.results
        ]

    def test_results_sorted_by_sequence(self):
        parse_result = ActionParseResult(
            turn_intent="观察",
            atomic_actions=[
                _make_action("a1", 1, True),
                _make_action("a2", 2, False),
                _make_action("a3", 3, True),
                _make_action("a4", 4, False),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(min_rolls=0))
        result = resolver.resolve(parse_result)

        sequences = [r.sequence for r in result.results]
        assert sequences == sorted(sequences)


class TestDifficulty:
    def _single_parse(self):
        return ActionParseResult(
            turn_intent="观察",
            atomic_actions=[_make_action("a1", 1, True)],
            anchors=[],
            ambiguities=[],
        )

    def _multi_parse(self):
        return ActionParseResult(
            turn_intent="观察",
            atomic_actions=[
                _make_action("a1", 1, True),
                _make_action("a2", 2, True),
            ],
            anchors=[],
            ambiguities=[],
        )

    def test_default_difficulty_is_hard(self):
        cfg = RollConfig()
        assert cfg.difficulty == "困难"

    def test_hard_rolls_1d20(self):
        resolver = RollResolver(RollConfig(difficulty="困难", seed=1, min_rolls=0))
        result = resolver.resolve(self._single_parse())

        assert result.results[0].dice == "1d20"
        assert len(result.results[0].all_raw_values) == 1
        assert result.results[0].raw_value == result.results[0].all_raw_values[0]
        assert result.dice_formula == "1d20"

    def test_normal_rolls_2d20kh(self):
        resolver = RollResolver(RollConfig(difficulty="普通", seed=1, min_rolls=0))
        result = resolver.resolve(self._single_parse())

        assert result.results[0].dice == "2d20kh"
        assert len(result.results[0].all_raw_values) == 2
        assert result.results[0].raw_value == max(result.results[0].all_raw_values)
        assert result.dice_formula == "1×2d20kh"

    def test_easy_rolls_3d20kh(self):
        resolver = RollResolver(RollConfig(difficulty="简单", seed=1, min_rolls=0))
        result = resolver.resolve(self._single_parse())

        assert result.results[0].dice == "3d20kh"
        assert len(result.results[0].all_raw_values) == 3
        assert result.results[0].raw_value == max(result.results[0].all_raw_values)
        assert result.dice_formula == "1×3d20kh"

    def test_normal_raw_value_is_max_of_two(self):
        parse = self._multi_parse()
        resolver = RollResolver(RollConfig(difficulty="普通", seed=42, min_rolls=0))
        result = resolver.resolve(parse)

        for r in result.results:
            assert len(r.all_raw_values) == 2
            assert r.raw_value == max(r.all_raw_values)

    def test_easy_raw_value_is_max_of_three(self):
        parse = self._multi_parse()
        resolver = RollResolver(RollConfig(difficulty="简单", seed=42, min_rolls=0))
        result = resolver.resolve(parse)

        for r in result.results:
            assert len(r.all_raw_values) == 3
            assert r.raw_value == max(r.all_raw_values)

    def test_seeded_normal_is_reproducible(self):
        parse = self._multi_parse()
        r1 = RollResolver(RollConfig(difficulty="普通", seed=99, min_rolls=0))
        r2 = RollResolver(RollConfig(difficulty="普通", seed=99, min_rolls=0))

        res1 = r1.resolve(parse)
        res2 = r2.resolve(parse)

        assert [r.value for r in res1.results] == [r.value for r in res2.results]
        assert [r.all_raw_values for r in res1.results] == [r.all_raw_values for r in res2.results]

    def test_seeded_easy_is_reproducible(self):
        parse = self._multi_parse()
        r1 = RollResolver(RollConfig(difficulty="简单", seed=99, min_rolls=0))
        r2 = RollResolver(RollConfig(difficulty="简单", seed=99, min_rolls=0))

        res1 = r1.resolve(parse)
        res2 = r2.resolve(parse)

        assert [r.value for r in res1.results] == [r.value for r in res2.results]
        assert [r.all_raw_values for r in res1.results] == [r.all_raw_values for r in res2.results]

    def test_modifiers_apply_after_keep_highest(self):
        parse = self._single_parse()
        modifier_batch = RollModifierBatch(
            modifiers=[
                RollModifier(
                    action_id="a1",
                    label="有利地形",
                    reason="当前位置提供清晰视野。",
                    value=3,
                )
            ]
        )

        resolver = RollResolver(RollConfig(difficulty="普通", seed=1, min_rolls=0))
        result = resolver.resolve(parse, modifier_batch=modifier_batch)

        raw = result.results[0].raw_value
        assert raw is not None
        assert raw == max(result.results[0].all_raw_values)
        assert result.results[0].value == min(20, raw + 3)

    def test_empty_actions_normal(self):
        parse = ActionParseResult(
            turn_intent="无行动",
            atomic_actions=[],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(difficulty="普通", min_rolls=0))
        result = resolver.resolve(parse)

        assert result.dice_formula == "0×2d20kh"
        assert result.total_rolls == 0

    def test_custom_dice_sides_with_difficulty(self):
        parse = self._single_parse()

        resolver = RollResolver(RollConfig(difficulty="简单", dice_sides=6, seed=1, min_rolls=0))
        result = resolver.resolve(parse)

        assert result.results[0].dice == "3d6kh"
        assert result.dice_formula == "1×3d6kh"
        assert result.dice_sides == 6
        for v in result.results[0].all_raw_values:
            assert 1 <= v <= 6
        assert result.results[0].raw_value == max(result.results[0].all_raw_values)

    def test_all_raw_values_present_for_hard(self):
        parse = self._single_parse()
        resolver = RollResolver(RollConfig(difficulty="困难", seed=1, min_rolls=0))
        result = resolver.resolve(parse)

        assert result.results[0].all_raw_values == [result.results[0].raw_value]


class TestNeverRolls:
    def _action(self, action_id, sequence, action_type, *, likely_needs_roll, risk_level=None):
        return AtomicAction(
            action_id=action_id,
            sequence=sequence,
            actor="主角",
            action_type=action_type,
            raw_text="行动",
            verb="行动",
            likely_needs_roll=likely_needs_roll,
            risk_level=risk_level,
            confidence=0.9,
        )

    def test_wait_is_never_rolled_even_if_marked(self):
        parse_result = ActionParseResult(
            turn_intent="等待",
            atomic_actions=[
                self._action("w1", 1, "wait", likely_needs_roll=True),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(min_rolls=2, seed=1))
        result = resolver.resolve(parse_result)

        assert result.total_rolls == 0

    def test_wait_excluded_from_forced_sampling(self):
        parse_result = ActionParseResult(
            turn_intent="观察并等待",
            atomic_actions=[
                self._action("a1", 1, "observe", likely_needs_roll=True),
                self._action("w1", 2, "wait", likely_needs_roll=False),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(min_rolls=2, seed=1))
        result = resolver.resolve(parse_result)

        assert result.total_rolls == 1
        assert {r.action_id for r in result.results} == {"a1"}

    def test_low_risk_move_is_never_rolled(self):
        parse_result = ActionParseResult(
            turn_intent="走过去",
            atomic_actions=[
                self._action("m1", 1, "move", likely_needs_roll=True, risk_level="low"),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(min_rolls=2, seed=1))
        result = resolver.resolve(parse_result)

        assert result.total_rolls == 0

    def test_high_risk_move_can_be_rolled(self):
        parse_result = ActionParseResult(
            turn_intent="冲过去",
            atomic_actions=[
                self._action("m1", 1, "move", likely_needs_roll=True, risk_level="high"),
            ],
            anchors=[],
            ambiguities=[],
        )

        resolver = RollResolver(RollConfig(min_rolls=0, seed=1))
        result = resolver.resolve(parse_result)

        assert result.total_rolls == 1
        assert result.results[0].action_id == "m1"
