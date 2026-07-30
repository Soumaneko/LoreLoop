import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import debug_server


STATIC_DIR = Path(__file__).parents[1] / "debug_static"


def test_debug_frontend_javascript_is_syntactically_valid():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "--check", str(STATIC_DIR / "app.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_debug_frontend_exposes_runtime_content():
    source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'event.step === "done"' in source
    for marker in (
        '["jobs", "后台任务"]',
        'Core Memory (${coreItems.length})',
        '持久化记忆快照',
        '/api/maintenance-jobs?limit=100',
        '/api/operations/recoverable',
        '/api/operations/${encodeURIComponent(operationId)}/ack',
        '/api/operations/${encodeURIComponent(operation.operation_id)}/events',
        'function resumeActivePipelineOperation()',
        'const hasActiveJobs = jobs.some',
        'turn.closing_prompt || ""',
        'data.pending_openings?.[0]',
        '/api/saves/switch',
        '/api/saves/create',
        '/api/pending/discard',
        'class="scene-discard"',
        'function renderNarrativeConsistency',
        'function renderCanonQueries',
        'function renderCanonEvidence',
        'canon_queries_generated: ["narrative_canon_query"]',
        'query:${item.canon_query_id}',
        'item.matched_terms || []',
        'item.paragraph_index',
        'isRevisionComposerTrace',
        'narrative_revision_started: ["narrative_compose"]',
        'await loadTrace(failedTraceId)',
        'item.incorrect_statement',
        'item.authoritative_fact',
        'item.required_revision',
        'Composer Revision Instruction',
        'narrative_revised: ["narrative_compose"]',
        'g.context_search_plan?.intents || []',
        'g.context_history_evidence || {}',
        'details("Search Intents"',
        'details("Confirmed History"',
        'details("完整 JSON"',
        '/api/init/campaign-setup',
        '"initialization_campaign_setup"',
        '"initialization_opening"',
        'handleInitializationPipelineEvent',
        'init-ability-name',
        'init-ability-description',
        'initCampaignSetup',
    ):
        assert marker in source
    assert "context_fact_memories" not in source

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert "<title>LoreLoop（续章）Debugger</title>" in html
    assert "LoreLoop · 续章" in html
    assert 'href="/debug-assets/styles.css"' in html
    assert 'src="/debug-assets/app.js"' in html
    assert 'id="saveSelect"' in html
    assert 'id="createSaveBtn"' in html
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    assert "select option, select optgroup" in styles

    render_outline = source[source.index("function renderOutline"):]
    assert render_outline.index('card("停点"') < render_outline.index(
        "outline.planned_events"
    )


def test_maintenance_jobs_debug_endpoint_parses_filters(monkeypatch):
    captured = {}

    def list_jobs(**kwargs):
        captured.update(kwargs)
        return [{"job_id": "job-1", "status": "failed"}]

    monkeypatch.setattr(debug_server, "list_maintenance_jobs", list_jobs)
    monkeypatch.setattr(
        debug_server,
        "get_campaign_runtime",
        lambda: SimpleNamespace(
            jobs=SimpleNamespace(status_counts=lambda: {"failed": 1})
        ),
    )
    handler = object.__new__(debug_server.DebugHandler)
    handler.path = "/api/maintenance-jobs?status=failed&job_type=scene_summary&limit=12"

    result = handler._load_maintenance_jobs()

    assert captured == {
        "status": "failed",
        "job_type": "scene_summary",
        "limit": 12,
    }
    assert result == {
        "jobs": [{"job_id": "job-1", "status": "failed"}],
        "status_counts": {"failed": 1},
    }
