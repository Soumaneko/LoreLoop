import json
from pathlib import Path
import pytest

from scripts.benchmark_narrative_quality import (
    DEFAULT_FIXTURES,
    ABCaseRecording,
    ABExperimentRecordings,
    ABSummaryReport,
    CaseRecording,
    CaseSummary,
    InvariantCheckResult,
    JudgeScore,
    NarrativeTestCase,
    PairSummary,
    check_invariants,
    check_recordings,
    generate_blind_eval_package,
    load_fixtures,
    summarize_ab_judgments,
)


def test_narrative_fixtures_load_and_validate():
    fixtures = load_fixtures(DEFAULT_FIXTURES)
    assert len(fixtures) == 9

    case_ids = [c.case_id for c in fixtures]
    assert len(case_ids) == len(set(case_ids))

    expected_categories = {
        "tavern_inquiry",
        "rogue_recruitment",
        "secret_investigation",
        "faction_confrontation",
        "dungeon_puzzle",
        "inn_rest_overhear",
        "moral_dilemma",
        "failed_action_recovery",
        "high_stakes_negotiation",
    }
    found_categories = {c.category for c in fixtures}
    assert found_categories == expected_categories


def test_invariant_checker_on_valid_output():
    fixtures = load_fixtures(DEFAULT_FIXTURES)
    case1 = fixtures[0]

    valid_output = {
        "planning_reasoning": "根据判定，萧欢成功打听到了关于妖兽精血的消息。",
        "must_establish_facts": ["孙掌柜告知了关于精血的流向与太玄门的关注"],
        "must_not_establish_facts": ["不得否认成功结果"],
        "information_boundaries": ["孙掌柜只知道有人在查，不知道具体买家底细"],
        "scene_stopping_point": "孙掌柜说完太玄门执事核查买家的线索后，低头擦拭茶具，等待萧欢的回应。",
        "planned_events": [
            {
                "event_id": "e1",
                "event_fact": "孙掌柜收下灵石并告知萧欢雷纹豹精血已被多方盯上，太玄门也在核查买家。",
                "linked_outcome_ids": ["act_01"],
                "involved_entities": ["孙掌柜"],
            }
        ],
        "scene_reasoning": "正文落实规划事实与对白。",
        "scene_text": "孙掌柜不动声色地将灵石扫入袖中，凑近低语道：“雷纹豹精血确实刚到落雁渡，但已被好几拨人盯上了，太玄门的外门执事甚至在私下打探买家背景，客官若要插手可得小心。”",
        "closing_prompt": "得知了精血被太玄门关注的消息，你会怎么做？",
        "next_action_directions": [
            {
                "direction": "向孙掌柜追问太玄门执事的动向",
                "rationale": "摸清潜在竞争对手或威胁",
                "anchors": ["太玄门", "执事"],
            }
        ],
    }

    inv = check_invariants(case1, valid_output)
    assert inv.passed is True
    assert inv.schema_valid is True
    assert inv.all_outcomes_linked is True
    # Note: stopping_point_respected at the deterministic gate level asserts non-empty
    # scene_text & scene_stopping_point. Deep semantic compliance is judged in blind eval.
    assert inv.stopping_point_respected is True
    assert inv.no_forbidden_phrases is True


def test_invariant_checker_catches_missing_outcomes_and_system_leaks():
    fixtures = load_fixtures(DEFAULT_FIXTURES)
    case1 = fixtures[0]

    # Missing linked_outcome_ids and leaked system phrase
    bad_output = {
        "planning_reasoning": "根据判定测试。",
        "must_establish_facts": ["事实1"],
        "must_not_establish_facts": [],
        "information_boundaries": [],
        "scene_stopping_point": "停点测试。",
        "planned_events": [
            {
                "event_id": "e1",
                "event_fact": "事件事实",
                "linked_outcome_ids": [],  # Missing act_01
                "involved_entities": [],
            }
        ],
        "scene_reasoning": "审计测试。",
        "scene_text": "根据 CoreMemory 的设定，掌柜说话了。",
        "closing_prompt": "继续？",
        "next_action_directions": [],
    }

    inv = check_invariants(case1, bad_output)
    assert inv.passed is False
    assert inv.all_outcomes_linked is False
    assert inv.no_forbidden_phrases is False
    assert any("Missing linked outcomes" in d for d in inv.details)
    assert any("System term 'CoreMemory' leaked" in d for d in inv.details)


def test_check_recordings_catches_unknown_missing_and_mismatched_inputs():
    fixtures = load_fixtures(DEFAULT_FIXTURES)
    case1 = fixtures[0]

    valid_output = {
        "planning_reasoning": "根据判定测试。",
        "must_establish_facts": ["事实1"],
        "must_not_establish_facts": [],
        "information_boundaries": [],
        "scene_stopping_point": "停点测试。",
        "planned_events": [
            {
                "event_id": "e1",
                "event_fact": "事件事实",
                "linked_outcome_ids": [case1.turn_resolution.action_resolutions[0].action_id],
                "involved_entities": [],
            }
        ],
        "scene_reasoning": "审计测试。",
        "scene_text": "正文内容。",
        "closing_prompt": "继续？",
        "next_action_directions": [],
    }

    # 1. Missing cases in recordings
    partial_recs = [
        CaseRecording(
            case_id=case1.case_id,
            category=case1.category,
            title=case1.title,
            user_input=case1.user_input,
            output=valid_output,
        )
    ]
    passed, msgs = check_recordings(fixtures, partial_recs)
    assert passed is False
    assert any("Case missing from recordings" in m for m in msgs)

    # 2. Unknown case ID in recordings
    unknown_rec = CaseRecording(
        case_id="case_999_unknown",
        category="unknown",
        title="Unknown",
        user_input="input",
        output=valid_output,
    )
    passed, msgs = check_recordings(fixtures, [unknown_rec])
    assert passed is False
    assert any("Unknown case ID in recordings" in m for m in msgs)

    # 3. Input mismatch between recording and fixture
    mismatched_rec = CaseRecording(
        case_id=case1.case_id,
        category=case1.category,
        title=case1.title,
        user_input="完全不同的旧用户输入",
        output=valid_output,
    )
    passed, msgs = check_recordings(fixtures, [mismatched_rec])
    assert passed is False
    assert any("user_input mismatch" in m for m in msgs)


def test_ab_blind_eval_packaging_and_mapping_isolation(tmp_path):
    fixtures = load_fixtures(DEFAULT_FIXTURES)
    assert len(fixtures) == 9

    # Construct mock 54 calls
    mock_recordings: list[ABCaseRecording] = []
    for rep in range(1, 4):
        for case in fixtures:
            for var in ["A", "B"]:
                mock_recordings.append(
                    ABCaseRecording(
                        variant=var,
                        case_id=case.case_id,
                        category=case.category,
                        title=case.title,
                        repetition=rep,
                        user_input=case.user_input,
                        output={
                            "scene_text": f"Mock output for {case.case_id} variant {var} rep {rep}.",
                            "closing_prompt": f"Mock closing for {var} {rep}?",
                            "next_action_directions": [] if var == "B" else [{"direction": "d", "rationale": "r", "anchors": []}],
                        },
                        token_usage={"total_tokens": 100},
                        latency_ms=500.0,
                        char_count=50,
                        invariant_check=InvariantCheckResult(
                            passed=True,
                            schema_valid=True,
                            all_outcomes_linked=True,
                            stopping_point_respected=True,
                            no_forbidden_phrases=True,
                        ),
                    )
                )

    exp_data = ABExperimentRecordings(
        timestamp="2026-08-26T00:00:00Z",
        task_a="narrative_compose",
        task_b="narrative_compose_open_affordance",
        total_calls=len(mock_recordings),
        recordings=mock_recordings,
    )

    prompts_dir = tmp_path / "eval_prompts"
    mapping_path = tmp_path / "mapping.json"

    mapping = generate_blind_eval_package(
        fixtures=fixtures,
        recordings_data=exp_data,
        output_dir=prompts_dir,
        mapping_path=mapping_path,
        seed=42,
    )

    # 1. Check generated Markdown files
    md_files = list(prompts_dir.glob("*.md"))
    assert len(md_files) == 9

    left_a_count = 0
    total_pairs = 0

    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        # Ensure no leaked variant tags or task names
        assert "narrative_compose_open_affordance" not in content
        assert "task_a" not in content
        assert "task_b" not in content
        assert "Variant A" not in content
        assert "Variant B" not in content
        assert "next_action_directions" not in content
        assert '"left_violations": ["<' not in content
        assert '"right_violations": ["<' not in content

    # 2. Check mapping balance
    for case_id, case_maps in mapping["mappings"].items():
        assert len(case_maps) == 3
        for pair_map in case_maps:
            total_pairs += 1
            if pair_map["left"] == "A":
                left_a_count += 1

    assert total_pairs == 27
    # Balanced check: 13 or 14 (difference <= 1)
    assert left_a_count in (13, 14)

    mock_recordings[0].invariant_check = InvariantCheckResult(
        passed=False,
        schema_valid=False,
        all_outcomes_linked=False,
        stopping_point_respected=False,
        no_forbidden_phrases=True,
    )
    with pytest.raises(ValueError, match="cannot include failed recordings"):
        generate_blind_eval_package(
            fixtures=fixtures,
            recordings_data=exp_data,
            output_dir=prompts_dir,
            mapping_path=mapping_path,
            seed=42,
        )


def test_ab_summary_majority_voting_and_violations(tmp_path):
    mapping_path = tmp_path / "mapping.json"
    judgments_dir = tmp_path / "judgments"
    recordings_path = tmp_path / "ab_recordings.json"
    judgments_dir.mkdir(parents=True, exist_ok=True)

    # Mock recordings matching evidence
    mock_recs = ABExperimentRecordings(
        timestamp="2026-08-26T00:00:00Z",
        total_calls=12,
        recordings=[
            ABCaseRecording(
                variant="A",
                case_id="case_01_tavern_inquiry",
                category="tavern",
                title="title",
                repetition=rep,
                user_input="input",
                output={"scene_text": "落雁渡茶楼", "closing_prompt": "。"},
            )
            for rep in [1, 2, 3]
        ]
        + [
            ABCaseRecording(
                variant="B",
                case_id="case_01_tavern_inquiry",
                category="tavern",
                title="title",
                repetition=rep,
                user_input="input",
                output={"scene_text": "青瓷茶壶", "closing_prompt": "。"},
            )
            for rep in [1, 2, 3]
        ]
        + [
            ABCaseRecording(
                variant="A",
                case_id="case_02_rogue_recruitment",
                category="rogue",
                title="title",
                repetition=rep,
                user_input="input",
                output={"scene_text": "盗贼招募", "closing_prompt": "。"},
            )
            for rep in [1, 2, 3]
        ]
        + [
            ABCaseRecording(
                variant="B",
                case_id="case_02_rogue_recruitment",
                category="rogue",
                title="title",
                repetition=rep,
                user_input="input",
                output={"scene_text": "暗巷密谋", "closing_prompt": "。"},
            )
            for rep in [1, 2, 3]
        ],
    )
    recordings_path.write_text(mock_recs.model_dump_json(), encoding="utf-8")

    # 2 cases in mapping
    mock_mapping = {
        "version": 1,
        "seed": 42,
        "timestamp": "2026-08-26T00:00:00Z",
        "total_cases": 2,
        "total_pairs": 6,
        "mappings": {
            "case_01_tavern_inquiry": [
                {"repetition": 1, "left": "A", "right": "B"},
                {"repetition": 2, "left": "B", "right": "A"},
                {"repetition": 3, "left": "A", "right": "B"},
            ],
            "case_02_rogue_recruitment": [
                {"repetition": 1, "left": "A", "right": "B"},
                {"repetition": 2, "left": "B", "right": "A"},
                {"repetition": 3, "left": "A", "right": "B"},
            ],
        },
    }
    mapping_path.write_text(json.dumps(mock_mapping), encoding="utf-8")

    # Case 1 judgments: B wins 2 pairs (rep 1 right, rep 2 left), A wins rep 3 (left) -> B wins case
    case1_judge = {
        "case_id": "case_01_tavern_inquiry",
        "pairs": [
            {
                "repetition": 1,
                "overall_preference": "right",  # B
                "continue_desire": "right",
                "open_affordance": "right",
                "surprising_but_coherent": "right",
                "npc_world_agency": "right",
                "specificity": "right",
                "left_evidence": "落雁渡茶楼",
                "right_evidence": "青瓷茶壶",
                "left_violations": [],
                "right_violations": [],
                "reason": "B is better",
            },
            {
                "repetition": 2,
                "overall_preference": "left",  # B
                "continue_desire": "left",
                "open_affordance": "left",
                "surprising_but_coherent": "left",
                "npc_world_agency": "left",
                "specificity": "left",
                "left_evidence": "青瓷茶壶",
                "right_evidence": "落雁渡茶楼",
                "left_violations": [],
                "right_violations": [],
                "reason": "B is better",
            },
            {
                "repetition": 3,
                "overall_preference": "left",  # A
                "continue_desire": "left",
                "open_affordance": "left",
                "surprising_but_coherent": "left",
                "npc_world_agency": "left",
                "specificity": "left",
                "left_evidence": "落雁渡茶楼",
                "right_evidence": "青瓷茶壶",
                "left_violations": [],
                "right_violations": [],
                "reason": "A is better",
            },
        ],
    }
    (judgments_dir / "case_01_tavern_inquiry.json").write_text(
        json.dumps(case1_judge), encoding="utf-8"
    )

    # Case 2 judgments:
    # Rep 1: A (left) preferred, but has left_violations -> Overridden to B win
    # Rep 2: B (left) wins
    # Rep 3: Both have violations -> Void pair
    # -> B wins 2 pairs -> B wins case
    case2_judge = {
        "case_id": "case_02_rogue_recruitment",
        "pairs": [
            {
                "repetition": 1,
                "overall_preference": "left",  # A was preferred
                "continue_desire": "left",
                "open_affordance": "left",
                "surprising_but_coherent": "left",
                "npc_world_agency": "left",
                "specificity": "left",
                "left_evidence": "盗贼招募",
                "right_evidence": "暗巷密谋",
                "left_violations": ["Player overstepping"],  # A violated!
                "right_violations": [],
                "reason": "A had a violation",
            },
            {
                "repetition": 2,
                "overall_preference": "left",  # B
                "continue_desire": "left",
                "open_affordance": "left",
                "surprising_but_coherent": "left",
                "npc_world_agency": "left",
                "specificity": "left",
                "left_evidence": "暗巷密谋",
                "right_evidence": "盗贼招募",
                "left_violations": [],
                "right_violations": [],
                "reason": "B is better",
            },
            {
                "repetition": 3,
                "overall_preference": "tie",
                "continue_desire": "tie",
                "open_affordance": "tie",
                "surprising_but_coherent": "tie",
                "npc_world_agency": "tie",
                "specificity": "tie",
                "left_evidence": "盗贼招募",
                "right_evidence": "暗巷密谋",
                "left_violations": ["A leak"],
                "right_violations": ["B hallucination"],  # Both violated -> Void
                "reason": "Both violated",
            },
        ],
    }
    (judgments_dir / "case_02_rogue_recruitment.json").write_text(
        json.dumps(case2_judge), encoding="utf-8"
    )

    report = summarize_ab_judgments(
        mapping_path=mapping_path,
        judgments_dir=judgments_dir,
        recordings_path=recordings_path,
    )

    assert report.total_cases == 2
    assert report.b_case_wins == 2
    assert report.a_case_wins == 0
    assert report.tie_cases == 0
    assert report.b_case_win_rate == 1.0

    # Check Case 2 pair details
    c2_summary = [c for c in report.cases if c.case_id == "case_02_rogue_recruitment"][0]
    assert c2_summary.winner == "B"
    assert c2_summary.pairs[0].winner == "B"  # Overridden by violation
    assert c2_summary.pairs[1].winner == "B"
    assert c2_summary.pairs[2].winner == "void"


def test_ab_summary_rejects_invalid_judgments(tmp_path):
    mapping_path = tmp_path / "mapping.json"
    judgments_dir = tmp_path / "judgments"
    judgments_dir.mkdir(parents=True, exist_ok=True)

    mock_mapping = {
        "version": 1,
        "seed": 42,
        "timestamp": "2026-08-26T00:00:00Z",
        "total_cases": 1,
        "total_pairs": 3,
        "mappings": {
            "case_01_tavern_inquiry": [
                {"repetition": 1, "left": "A", "right": "B"},
                {"repetition": 2, "left": "B", "right": "A"},
                {"repetition": 3, "left": "A", "right": "B"},
            ],
        },
    }
    mapping_path.write_text(json.dumps(mock_mapping), encoding="utf-8")

    # Invalid: missing repetition 3
    invalid_judge = {
        "case_id": "case_01_tavern_inquiry",
        "pairs": [
            {
                "repetition": 1,
                "overall_preference": "right",
                "continue_desire": "right",
                "open_affordance": "right",
                "surprising_but_coherent": "right",
                "npc_world_agency": "right",
                "specificity": "right",
                "left_evidence": "证据1",
                "right_evidence": "证据2",
                "left_violations": [],
                "right_violations": [],
                "reason": "Pair 1 comparison",
            },
            {
                "repetition": 2,
                "overall_preference": "left",
                "continue_desire": "left",
                "open_affordance": "left",
                "surprising_but_coherent": "left",
                "npc_world_agency": "left",
                "specificity": "left",
                "left_evidence": "证据1",
                "right_evidence": "证据2",
                "left_violations": [],
                "right_violations": [],
                "reason": "Pair 2 comparison",
            },
        ],
    }
    (judgments_dir / "case_01_tavern_inquiry.json").write_text(
        json.dumps(invalid_judge), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Invalid judgment format"):
        summarize_ab_judgments(
            mapping_path=mapping_path,
            judgments_dir=judgments_dir,
        )

    # Invalid: all repetitions exist, but mandatory violation arrays and reason are absent.
    invalid_judge["pairs"].append(
        {
            "repetition": 3,
            "overall_preference": "tie",
            "continue_desire": "tie",
            "open_affordance": "tie",
            "surprising_but_coherent": "tie",
            "npc_world_agency": "tie",
            "specificity": "tie",
            "left_evidence": "证据1",
            "right_evidence": "证据2",
        }
    )
    for pair in invalid_judge["pairs"]:
        pair.pop("left_violations", None)
        pair.pop("right_violations", None)
        pair.pop("reason", None)
    (judgments_dir / "case_01_tavern_inquiry.json").write_text(
        json.dumps(invalid_judge), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Invalid judgment format"):
        summarize_ab_judgments(
            mapping_path=mapping_path,
            judgments_dir=judgments_dir,
        )


def test_ab_summary_detects_cross_pair_pollution(tmp_path):
    mapping_path = tmp_path / "mapping.json"
    judgments_dir = tmp_path / "judgments"
    recordings_path = tmp_path / "ab_recordings.json"
    judgments_dir.mkdir(parents=True, exist_ok=True)

    mock_mapping = {
        "version": 1,
        "seed": 42,
        "timestamp": "2026-08-26T00:00:00Z",
        "total_cases": 1,
        "total_pairs": 3,
        "mappings": {
            "case_01_tavern_inquiry": [
                {"repetition": 1, "left": "A", "right": "B"},
                {"repetition": 2, "left": "B", "right": "A"},
                {"repetition": 3, "left": "A", "right": "B"},
            ],
        },
    }
    mapping_path.write_text(json.dumps(mock_mapping), encoding="utf-8")

    # Create mock recordings with distinct texts for each repetition
    mock_recs = ABExperimentRecordings(
        timestamp="2026-08-26T00:00:00Z",
        total_calls=6,
        recordings=[
            ABCaseRecording(
                variant="A",
                case_id="case_01_tavern_inquiry",
                category="tavern",
                title="title",
                repetition=1,
                user_input="input",
                output={"scene_text": "第一回A正文独有文本", "closing_prompt": "。"},
            ),
            ABCaseRecording(
                variant="B",
                case_id="case_01_tavern_inquiry",
                category="tavern",
                title="title",
                repetition=1,
                user_input="input",
                output={"scene_text": "第一回B正文独有文本", "closing_prompt": "。"},
            ),
            ABCaseRecording(
                variant="A",
                case_id="case_01_tavern_inquiry",
                category="tavern",
                title="title",
                repetition=2,
                user_input="input",
                output={"scene_text": "第二回A正文独有文本", "closing_prompt": "。"},
            ),
            ABCaseRecording(
                variant="B",
                case_id="case_01_tavern_inquiry",
                category="tavern",
                title="title",
                repetition=2,
                user_input="input",
                output={"scene_text": "第二回B正文独有文本", "closing_prompt": "。"},
            ),
            ABCaseRecording(
                variant="A",
                case_id="case_01_tavern_inquiry",
                category="tavern",
                title="title",
                repetition=3,
                user_input="input",
                output={"scene_text": "第三回A青阳坊百草阁暗桩独有文本", "closing_prompt": "。"},
            ),
            ABCaseRecording(
                variant="B",
                case_id="case_01_tavern_inquiry",
                category="tavern",
                title="title",
                repetition=3,
                user_input="input",
                output={"scene_text": "第三回B正文独有文本", "closing_prompt": "。"},
            ),
        ],
    )
    recordings_path.write_text(mock_recs.model_dump_json(), encoding="utf-8")

    # Pair 2 mistakenly quotes text from Pair 3 (cross-pair pollution!)
    polluted_judge = {
        "case_id": "case_01_tavern_inquiry",
        "pairs": [
            {
                "repetition": 1,
                "overall_preference": "left",
                "continue_desire": "left",
                "open_affordance": "left",
                "surprising_but_coherent": "left",
                "npc_world_agency": "left",
                "specificity": "left",
                "left_evidence": "第一回A正文",
                "right_evidence": "第一回B正文",
                "left_violations": [],
                "right_violations": [],
                "reason": "Pair 1",
            },
            {
                "repetition": 2,
                "overall_preference": "right",
                "continue_desire": "right",
                "open_affordance": "right",
                "surprising_but_coherent": "right",
                "npc_world_agency": "right",
                "specificity": "right",
                "left_evidence": "第二回B正文",
                # Leaked from Rep 3!
                "right_evidence": "第三回A青阳坊百草阁",
                "left_violations": [],
                "right_violations": [],
                "reason": "Pair 2 leaked from Pair 3",
            },
            {
                "repetition": 3,
                "overall_preference": "left",
                "continue_desire": "left",
                "open_affordance": "left",
                "surprising_but_coherent": "left",
                "npc_world_agency": "left",
                "specificity": "left",
                "left_evidence": "第三回A青阳坊",
                "right_evidence": "第三回B正文",
                "left_violations": [],
                "right_violations": [],
                "reason": "Pair 3",
            },
        ],
    }
    (judgments_dir / "case_01_tavern_inquiry.json").write_text(
        json.dumps(polluted_judge), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Cross-pair pollution detected"):
        summarize_ab_judgments(
            mapping_path=mapping_path,
            judgments_dir=judgments_dir,
            recordings_path=recordings_path,
        )
