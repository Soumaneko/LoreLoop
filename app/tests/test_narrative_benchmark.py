import json

import pytest

from app.core.llm.config import GatewayConfig
from app.core.llm.gateway import LLMGateway
from app.core.llm.prompts import PromptLoader
from app.core.llm.providers import MockProvider
from app.core.llm.tracing import TraceLogger
from app.tests.test_narrative_composer import _context, _output
from scripts import benchmark_narrative_quality as benchmark


def test_suite_recording_preserves_outputs_and_rejects_stale_run(tmp_path, monkeypatch):
    case = benchmark.NarrativeTestCase(
        case_id="recording_case", category="smoke", title="记录保存", description="检查实际录制路径",
        turn_index=1, **_context().model_dump(),
    )
    source = tmp_path / "case.json"
    source.write_text(case.model_dump_json(), encoding="utf-8")
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"cases": [{"path": source.name, "case_id": case.case_id}]}), encoding="utf-8")
    cases = benchmark.load_fixtures(suite)
    output = _output()
    output["npc_usages"] = [{"name": "赵七", "surfaces": ["人群"], "presence": "appeared"}]
    output["director_usages"] = ["event_1"]
    constructed = []

    def gateway_factory(**kwargs):
        constructed.append(kwargs)
        provider = MockProvider()
        provider.set_responses([json.dumps(output, ensure_ascii=False)])
        return lambda: LLMGateway(
            provider=provider, prompt_loader=PromptLoader(kwargs["prompts_dir"]),
            tracer=TraceLogger(kwargs["log_path"]),
        )

    monkeypatch.setattr(benchmark, "SharedGatewayFactory", gateway_factory)
    monkeypatch.setattr(benchmark, "LOCAL_DIR", tmp_path / "local")
    run_dir = benchmark.LOCAL_DIR / "run"
    config = GatewayConfig(api_key="test", model="mock")
    records = benchmark.record_run(cases, config, run_dir)
    assert records[0]["success"] is True
    saved = json.loads((run_dir / case.case_id / "scene-result.json").read_text(encoding="utf-8"))
    assert saved["npc_usages"] == output["npc_usages"]
    assert saved["director_usages"] == output["director_usages"]
    assert records[0]["director_usages"] == output["director_usages"]
    assert saved["scene_text"] == output["scene_text"]
    assert json.loads((run_dir / "fixtures.json").read_text(encoding="utf-8"))[0]["case_id"] == case.case_id
    assert json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["prompt_sha256"]
    with pytest.raises(FileExistsError):
        benchmark.record_run(cases, config, run_dir)
    assert len(constructed) == 1
    assert json.loads((run_dir / case.case_id / "scene-result.json").read_text(encoding="utf-8")) == saved


@pytest.mark.parametrize("added_field", ["npc_usages", "director_usages"])
def test_ab_frozen_versions_failure_pairing_and_blind_report(tmp_path, monkeypatch, added_field):
    from collections import Counter
    from scripts import benchmark_npc_usages_ab as ab

    monkeypatch.setattr(benchmark, "LOCAL_DIR", tmp_path / "local")
    run_dir = benchmark.LOCAL_DIR / "ab"
    config = GatewayConfig(api_key="unit-test-secret", model="mock")
    plan = ab.read(ab.ASSETS / 'plan.json')
    if added_field == 'director_usages':
        plan.update(added_field=added_field, experiment=added_field,
                    baseline_revision=plan.pop('candidate_revision'))
    metadata = ab.prepare(run_dir, config, plan=plan)
    assert len(metadata["plan"]["case_ids"]) == 10
    assert len(metadata["jobs"]) == 60
    assert "unit-test-secret" not in json.dumps(metadata)
    assert added_field not in ab.read(run_dir / "A/schema.json")["properties"]
    assert added_field in ab.read(run_dir / "B/schema.json")["required"]
    with pytest.raises(FileExistsError):
        ab.prepare(run_dir, config)
    cases = {c.case_id: c for c in benchmark.load_fixtures(run_dir / "fixtures.json")}
    constructed = []

    def gateway_factory(**kwargs):
        from pathlib import Path
        folder = Path(kwargs["log_path"]).parent
        cid, rep, variant = folder.parent.parent.name, int(folder.parent.name), folder.name
        constructed.append((cid, rep, variant))
        case = cases[cid]
        output = _output()
        output["action_resolutions"] = [
            {"action_id": a.action_id, "outcome_band": a.outcome_band, "resolved_fact": "行动按给定结果完成。"}
            for a in case.action_judgments
        ]
        output["planned_events"][0]["linked_outcome_ids"] = [a.action_id for a in case.action_judgments]
        output["scene_text"] = "你看向门口，脚步声由远而近。" if variant == "A" else "你侧耳听着，门外的脚步停了。"
        # Preserve one failed B call and keep approximate surfaces acceptable elsewhere.
        if variant == "A" or (cid == metadata["plan"]["case_ids"][0] and rep == 1):
            output.pop(added_field)
        else:
            output["npc_usages"] = [{"name": "赵七", "surfaces": ["概括称呼"], "presence": "appeared"}]
            if added_field == 'director_usages':
                output[added_field] = ['event_1']
        provider = MockProvider()
        provider.set_responses([json.dumps(output, ensure_ascii=False)] * 2)
        return lambda: LLMGateway(
            provider=provider, prompt_loader=PromptLoader(kwargs["prompts_dir"]),
            tracer=TraceLogger(kwargs["log_path"]), default_model="mock",
        )

    monkeypatch.setattr(ab, "SharedGatewayFactory", gateway_factory)
    records = ab.record(run_dir, config)
    assert len(records) == 60
    failed = [r for r in records if r["error"]]
    assert len(failed) == 1 and failed[0]["variant"] == "B" and failed[0]["attempts"] == 2
    assert all(added_field not in r["output"] for r in records if r["variant"] == "A")
    assert len(ab.record(run_dir, config)) == 60 and len(constructed) == 60
    mapping = ab.pack(run_dir)
    assert len(mapping["pairs"]) == 29 and len(mapping["excluded"]) == 1
    sides = Counter(p["left"] for p in mapping["pairs"])
    assert abs(sides["A"] - sides["B"]) <= 1
    for p in (run_dir / "blind").glob("*.md"):
        packet = p.read_text(encoding="utf-8")
        assert "npc_usages" not in packet and "director_reason" not in packet
        assert "scene_reasoning" not in packet and "output_schema_json" not in packet
    report_dir = run_dir / "analysis"
    kwargs = dict(judge="mock", date="2026-11-04", mode="mock")
    with pytest.raises(ValueError, match="incomplete"):
        ab.report(run_dir, report_dir, **kwargs)
    indexed = {ab.key(r): r for r in records}
    for cid in metadata["plan"]["case_ids"]:
        pairs = []
        for m in mapping["pairs"]:
            if m["case_id"] != cid:
                continue
            sides = {}
            for side in ("left", "right"):
                body = indexed[cid, m["repetition"], m[side]]["output"]["scene_text"]
                sides[side] = {d: {"score": 2, "evidence": body, "reason": "场景基本成立。"} for d in ab.DIMENSIONS}
                sides[side]["violations"] = []
            pairs.append(dict(repetition=m["repetition"], **sides,
                              preference="left" if m["left"] == "B" else "right", reason="右侧或左侧的具体互动更充分。"))
        ab.write(run_dir / "judgments" / f"{cid}.json", dict(case_id=cid, pairs=pairs))
    first = run_dir / "judgments" / f"{metadata['plan']['case_ids'][0]}.json"
    saved_judgment = ab.read(first)
    bad_judgment = ab.read(first)
    bad_judgment["pairs"][0]["left"]["scene_construction"]["evidence"] = "这句没有出现在正文。"
    ab.write(first, bad_judgment)
    with pytest.raises(ValueError, match="evidence absent"):
        ab.report(run_dir, report_dir, **kwargs)
    ab.write(first, saved_judgment)
    result = ab.report(run_dir, report_dir, **kwargs)
    assert result["pair_votes"] == {"B": 29}
    assert result["case_votes"] == {"B": 10}
    assert result["performance"]["B"]["failures"] == 1
    assert result["performance"]["A"]["metrics"]["total_tokens"] == {"n": 0, "mean": None, "median": None}
    assert "scene_text" not in (report_dir / "report.json").read_text(encoding="utf-8")
    saved = (run_dir / "A/prompt.md").read_text(encoding="utf-8")
    (run_dir / "A/prompt.md").write_text(saved + "changed", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen file changed"):
        ab.pack(run_dir)
