"""Prepare, record, blind-pack and summarize Composer field-addition experiments."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pydantic import BaseModel, ConfigDict, Field, StrictInt
from typing import Literal
from app.core.llm.config import GatewayConfig, SharedGatewayFactory
from app.core.llm.prompts import PromptLoader
from app.core.llm.schema import schema_json
from app.core.llm.token_usage import normalize_token_usage
from scripts.benchmark_narrative_quality import load_fixtures, check_invariants, require_local_path

BENCH = ROOT / 'benchmarks/narrative_quality'
ASSETS = BENCH / 'experiments/npc-usages'
DIMENSIONS = ('scene_construction', 'causal_interaction', 'scene_development', 'playability')
SOURCES = {
    'scene_writer_schemas': 'app/domain/scene_writer/schemas.py',
    'schemas': 'app/domain/narrative_composer/schemas.py',
    'runner': 'app/domain/narrative_composer/runner.py',
}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding="utf-8")
    temporary.replace(path)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def service_config(service):
    from app.runtime.model_config import ModelConfigStore
    matches = [c for c in ModelConfigStore().load_set().services
               if service in {c.config_id, c.preset_id}]
    if len(matches) != 1:
        raise ValueError('service must identify exactly one saved configuration')
    return matches[0].gateway_config()


def config_identity(config):
    return dict({key: getattr(config, key) for key in
                 ('model', 'protocol', 'thinking', 'reasoning_effort', 'request_profile', 'timeout')},
                endpoint_hash=digest(config.base_url))


def frozen_runner(folder):
    # Isolate the three frozen modules; production imports and globals stay untouched.
    package = '_npc_ab_' + digest(str(folder.resolve()))[:16]
    for name in ('scene_writer_schemas', 'schemas', 'runner'):
        qualified = package + '.' + name
        if qualified in sys.modules:
            continue
        path = folder / (name + '.py')
        source = path.read_text(encoding="utf-8").replace(
            'from app.domain.scene_writer.schemas import', f'from {package}.scene_writer_schemas import'
        ).replace('from app.domain.narrative_composer.schemas import', f'from {package}.schemas import')
        module = types.ModuleType(qualified)
        module.__file__ = str(path)
        sys.modules[qualified] = module
        exec(compile(source, str(path), 'exec'), module.__dict__)
    return sys.modules[package + '.runner']


def make_plan(seed=42, repetitions=3):
    fixed = [c.case_id for c in load_fixtures(ASSETS / 'suite.json')]
    pool = sorted(p.parent.name for p in (BENCH / 'cases').glob('*/input.json')
                  if p.parent.name not in fixed)
    drawn = sorted(random.Random(seed).sample(pool, 4))
    return {
        'experiment': 'npc_usages', 'seed': seed, 'repetitions': repetitions,
        'baseline_revision': git('rev-parse', 'HEAD').decode().strip(),
        'fixed_case_ids': fixed, 'sampling_pool': pool, 'drawn_case_ids': drawn,
        'case_ids': sorted(fixed + drawn),
        'variants': {'A': '新增 npc_usages 前的 Composer', 'B': '当前姓名版 npc_usages Composer'},
        'temperature': 0.8, 'max_retries': 1,
    }


def prepare(run_dir, config, service='opencode-go', *, plan=None):
    plan = plan or read(ASSETS / 'plan.json')
    cases = [load_fixtures(BENCH / 'cases' / cid / 'input.json')[0] for cid in plan['case_ids']]
    if plan['repetitions'] < 1:
        raise ValueError('repetitions must be positive')
    run_dir = require_local_path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    write(run_dir / 'fixtures.json', [c.model_dump(mode='json') for c in cases])
    shutil.copyfile(ASSETS / 'judge.md', run_dir / 'judge.md')
    (run_dir / 'judgment.schema.json').write_text(schema_json(CaseJudgment), encoding="utf-8")
    templates, schemas, runners = {}, {}, {}
    for variant in ('A', 'B'):
        folder = run_dir / variant
        folder.mkdir()
        revision = plan['baseline_revision'] if variant == 'A' else plan.get('candidate_revision')
        for name, path in SOURCES.items():
            content = git('show', f"{revision}:{path}") if revision else (ROOT / path).read_bytes()
            (folder / (name + '.py')).write_bytes(content)
        prompt_dir = folder / 'prompts'
        if revision:
            for path in git('ls-tree', '-r', '--name-only', revision, 'app/prompts').decode().splitlines():
                target = prompt_dir / Path(path).relative_to('app/prompts')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(git('show', f"{revision}:{path}"))
        else:
            shutil.copytree(ROOT / 'app/prompts', prompt_dir)
        runners[variant] = frozen_runner(folder)
        templates[variant] = PromptLoader(str(prompt_dir)).load('narrative_compose')[0]
        schemas[variant] = json.loads(schema_json(runners[variant]._HardComposerResult))
        (folder / 'prompt.md').write_text(templates[variant], encoding="utf-8")
        write(folder / 'schema.json', schemas[variant])
    added_field = plan.get('added_field', 'npc_usages')
    if ''.join(line for line in templates['B'].splitlines(keepends=True)
               if not line.startswith(f'- {added_field}：')) != templates['A']:
        raise ValueError(f'A/B Prompt difference is not limited to the {added_field} instruction')
    a_properties, b_properties = schemas['A']['properties'], dict(schemas['B']['properties'])
    if added_field in a_properties or added_field not in b_properties:
        raise ValueError(f'A must omit {added_field}; B must request it')
    b_properties.pop(added_field)
    if a_properties != b_properties:
        raise ValueError('other output fields differ between A/B')
    if schemas['A']['required'] != [k for k in schemas['B']['required'] if k != added_field]:
        raise ValueError('other required fields differ between A/B')
    inputs = {}
    for case in cases:
        sides = []
        for variant in ('A', 'B'):
            runner = runners[variant]
            context = runner.NarrativeComposerContext.model_validate(case.model_dump(mode='json'))
            sides.append(runner.composer_context_input(context, case.recent_scene, case.turn_index))
        if sides[0] != sides[1]:
            raise ValueError(f'A/B case inputs differ: {case.case_id}')
        inputs[case.case_id] = digest(canonical(sides[0]))
    pairs = [(c.case_id, rep) for c in cases for rep in range(1, plan['repetitions'] + 1)]
    rng = random.Random(plan['seed'])
    rng.shuffle(pairs)
    starts = (['A', 'B'] * len(pairs))[:len(pairs)]
    rng.shuffle(starts)
    jobs = [{'case_id': cid, 'repetition': rep, 'variant': v}
            for (cid, rep), first in zip(pairs, starts)
            for v in (first, 'B' if first == 'A' else 'A')]
    metadata = {'plan': plan, 'service': service, 'config': config_identity(config),
                'case_input_hashes': inputs, 'jobs': jobs,
                'files': {str(p.relative_to(run_dir)): digest(p.read_bytes())
                          for p in sorted(run_dir.rglob('*')) if p.is_file()}}
    write(run_dir / 'run.json', metadata)
    return metadata


def key(record):
    return record['case_id'], record['repetition'], record['variant']


def load_run(run_dir, *, complete=False):
    run_dir = require_local_path(run_dir)
    metadata = read(run_dir / 'run.json')
    for name, expected in metadata['files'].items():
        if digest((run_dir / name).read_bytes()) != expected:
            raise ValueError(f'frozen file changed: {name}')
    records = read(run_dir / 'recordings.json') if (run_dir / 'recordings.json').exists() else []
    indexed = {key(r): r for r in records}
    expected = {key(j) for j in metadata['jobs']}
    if len(indexed) != len(records) or not set(indexed) <= expected:
        raise ValueError('duplicate or unexpected recording')
    if any(r['input_hash'] != metadata['case_input_hashes'][r['case_id']] for r in records):
        raise ValueError('recorded case input hash differs from preparation')
    if complete and set(indexed) != expected:
        raise ValueError('recording is incomplete')
    return metadata, indexed


def measure(output, attempts):
    body = output.get('scene_text', '') if output else ''
    values = {'body_chars': len(body), 'body_nonwhitespace_chars': sum(not c.isspace() for c in body),
              'structured_chars': len(canonical({k: v for k, v in (output or {}).items() if k != 'scene_text'})),
              'npc_usages_chars': len(canonical(output['npc_usages'])) if output and 'npc_usages' in output else 0,
              'director_usages_chars': len(canonical(output['director_usages'])) if output and 'director_usages' in output else 0}
    usages = [normalize_token_usage(a.get('token_usage')) for a in attempts]
    for name in ('input_tokens', 'output_tokens', 'total_tokens'):
        values[name] = sum(u[name] for u in usages) if usages and all(u[name] is not None for u in usages) else None
    return values


def record(run_dir, config):
    run_dir = Path(run_dir)
    metadata, indexed = load_run(run_dir)
    if metadata['config'] != config_identity(config):
        raise ValueError('model configuration differs from preparation')
    cases = {c.case_id: c for c in load_fixtures(run_dir / 'fixtures.json')}
    runners = {v: frozen_runner(run_dir / v) for v in ('A', 'B')}
    records = list(indexed.values())
    for job in metadata['jobs']:
        if key(job) in indexed:
            continue
        cid, rep, variant = key(job)
        case, runner = cases[cid], runners[variant]
        folder = run_dir / 'calls' / cid / str(rep) / variant
        # An unfinished call may already have reached the provider; inspect its trace before retrying.
        folder.mkdir(parents=True, exist_ok=False)
        started = time.perf_counter()
        timings, warnings = [], []
        def on_start(number):
            timings.append({'attempt': number, 'scene_start_ms': None})
        def on_delta(text):
            if text and timings[-1]['scene_start_ms'] is None:
                timings[-1]['scene_start_ms'] = (time.perf_counter() - started) * 1000
        output, error = None, None
        try:
            gateway = SharedGatewayFactory(config=config, prompts_dir=str(run_dir / variant / 'prompts'),
                                           log_path=str(folder / 'traces.jsonl'))()
            gateway.set_trace_context(turn_index=case.turn_index, session_type='narrative_ab')
            context = runner.NarrativeComposerContext.model_validate(case.model_dump(mode='json'))
            inputs = runner.composer_context_input(context, case.recent_scene, case.turn_index)
            if digest(canonical(inputs)) != metadata['case_input_hashes'][cid]:
                raise ValueError('rendered case input changed after preparation')
            print(f'{cid}/{rep}/{variant}: started', flush=True)
            result = runner.run_narrative_composition(
                gateway, context=context, recent_scene=case.recent_scene, turn_index=case.turn_index,
                trace_id=f'{metadata["plan"]["experiment"]}_{cid}_{rep}_{variant}', on_stream_start=on_start,
                on_scene_text_delta=on_delta, on_warning=lambda w: warnings.append(w.code))
            output = result.model_dump(mode='json')
            checked = check_invariants(case, output)
            if not checked.passed:
                raise ValueError('; '.join(checked.details))
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
        path = folder / 'traces.jsonl'
        attempts = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
        expected_schema = json.loads((run_dir / variant / 'schema.json').read_text(encoding="utf-8"))
        for attempt in attempts:
            actual = dict(attempt['input_data'])
            actual_schema = json.loads(actual.pop('output_schema_json'))
            if digest(canonical(actual)) != metadata['case_input_hashes'][cid] or actual_schema != expected_schema:
                raise ValueError('actual LLM input does not match the frozen variant')
        item = dict(job, output=output, error=error, input_hash=metadata['case_input_hashes'][cid],
                    latency_ms=round((time.perf_counter() - started) * 1000, 2),
                    attempts=len(attempts), retries=max(0, len(attempts) - 1), warnings=warnings,
                    scene_start_ms=timings[-1]['scene_start_ms'] if timings and not error else None,
                    finish_reason=attempts[-1].get('finish_reason') if attempts else None,
                    truncated=attempts[-1].get('truncated') if attempts else None,
                    metrics=measure(output, attempts))
        records.append(item)
        write(folder / 'result.json', item)
        write(run_dir / 'recordings.json', records)
        print(f'{cid}/{rep}/{variant}: {error or str(item["metrics"]["body_chars"]) + " chars"}', flush=True)
    return records


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Score(StrictModel):
    score: StrictInt = Field(ge=0, le=4)
    evidence: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Violation(StrictModel):
    severity: Literal['local', 'major']
    category: Literal['outcome', 'continuity', 'player_agency', 'disclosure', 'internal_logic']
    evidence: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Assessment(StrictModel):
    scene_construction: Score
    causal_interaction: Score
    scene_development: Score
    playability: Score
    violations: list[Violation]


class PairJudgment(StrictModel):
    repetition: StrictInt = Field(ge=1)
    left: Assessment
    right: Assessment
    preference: Literal['left', 'right', 'tie']
    reason: str = Field(min_length=1)


class CaseJudgment(StrictModel):
    case_id: str
    pairs: list[PairJudgment]


def pack(run_dir):
    run_dir = Path(run_dir)
    metadata, indexed = load_run(run_dir, complete=True)
    valid, excluded = [], []
    for cid in metadata['plan']['case_ids']:
        for rep in range(1, metadata['plan']['repetitions'] + 1):
            if any(indexed[cid, rep, v]['error'] for v in ('A', 'B')):
                excluded.append({'case_id': cid, 'repetition': rep, 'reason': 'generation failure'})
            else:
                valid.append((cid, rep))
    sides = (['A', 'B'] * len(valid))[:len(valid)]
    random.Random(metadata['plan']['seed'] + 1).shuffle(sides)
    mappings = [dict(case_id=cid, repetition=rep, left=v, right='B' if v == 'A' else 'A')
                for (cid, rep), v in zip(valid, sides)]
    packets = run_dir / 'blind'
    packets.mkdir(exist_ok=False)
    cases = {c.case_id: c for c in load_fixtures(run_dir / 'fixtures.json')}
    for cid in metadata['plan']['case_ids']:
        pairs = [m for m in mappings if m['case_id'] == cid]
        if not pairs:
            continue
        case = cases[cid]
        context = frozen_runner(run_dir / 'A').composer_context_input(
            frozen_runner(run_dir / 'A').NarrativeComposerContext.model_validate(case.model_dump(mode='json')),
            case.recent_scene, case.turn_index)
        parts = [(run_dir / 'judge.md').read_text(encoding="utf-8"), '\n# 返回 JSON Schema\n', (run_dir / 'judgment.schema.json').read_text(encoding="utf-8"),
                 f'\n# case_id\n{cid}\n# 共同权威上下文\n', json.dumps(context, ensure_ascii=False, indent=2)]
        for item in pairs:
            parts.append(f'\n# Pair {item["repetition"]}\n')
            for side in ('left', 'right'):
                parts.append(f'\n## {side}\n' + indexed[cid, item['repetition'], item[side]]['output']['scene_text'])
        (packets / f'{cid}.md').write_text('\n'.join(parts), encoding="utf-8")
    mapping = {'pairs': mappings, 'excluded': excluded,
               'recordings_hash': digest((run_dir / 'recordings.json').read_bytes()),
               'packet_hashes': {p.name: digest(p.read_bytes()) for p in packets.glob('*.md')}}
    write(run_dir / 'mapping.json', mapping)
    (run_dir / 'judgments').mkdir()
    return mapping


def stats(values):
    values = [v for v in values if v is not None]
    return {'n': len(values), 'mean': statistics.mean(values) if values else None,
            'median': statistics.median(values) if values else None}


def report(run_dir, output_dir=None, *, judge, date, mode):
    from datetime import date as Date
    Date.fromisoformat(date)
    run_dir = Path(run_dir)
    output_dir = require_local_path(output_dir if output_dir is not None else run_dir / 'analysis')
    metadata, indexed = load_run(run_dir, complete=True)
    mapping = read(run_dir / 'mapping.json')
    if digest((run_dir / 'recordings.json').read_bytes()) != mapping['recordings_hash']:
        raise ValueError('recordings changed after blind packing')
    for name, expected in mapping['packet_hashes'].items():
        if digest((run_dir / 'blind' / name).read_bytes()) != expected:
            raise ValueError('blind packet changed')
    judgments = {}
    for path in sorted((run_dir / 'judgments').glob('*.json')):
        case = CaseJudgment.model_validate(read(path))
        for pair in case.pairs:
            k = case.case_id, pair.repetition
            if k in judgments:
                raise ValueError(f'duplicate judgment: {k}')
            judgments[k] = pair
    if set(judgments) != {(p['case_id'], p['repetition']) for p in mapping['pairs']}:
        raise ValueError('judgments are incomplete or contain unexpected pairs')
    pairs = []
    for m in mapping['pairs']:
        judgment = judgments[m['case_id'], m['repetition']]
        scores, violations = {}, {}
        for side in ('left', 'right'):
            assessment = getattr(judgment, side)
            body = indexed[m['case_id'], m['repetition'], m[side]]['output']['scene_text']
            for item in [*(getattr(assessment, d) for d in DIMENSIONS), *assessment.violations]:
                if item.evidence not in body:
                    raise ValueError(f'evidence absent from {m["case_id"]}/{m["repetition"]}/{side}')
            scores[m[side]] = {d: getattr(assessment, d).score for d in DIMENSIONS}
            scores[m[side]]['total'] = sum(scores[m[side]].values())
            violations[m[side]] = [v.model_dump() for v in assessment.violations]
        winner = 'tie' if judgment.preference == 'tie' else m[judgment.preference]
        pairs.append(dict(m, winner=winner, scores=scores, violations=violations,
                          judgment=judgment.model_dump()))
    performance = {}
    for variant in ('A', 'B'):
        records = [r for r in indexed.values() if r['variant'] == variant]
        successful = [r for r in records if not r['error']]
        performance[variant] = {
            'calls': len(records), 'failures': len(records) - len(successful),
            'metrics': {name: stats([r['metrics'].get(name) for r in
                                     (records if name.endswith('_tokens') else successful)]) for name in
                        ('body_chars', 'body_nonwhitespace_chars', 'structured_chars', 'npc_usages_chars', 'director_usages_chars',
                         'input_tokens', 'output_tokens', 'total_tokens')},
            'latency_ms': stats([r['latency_ms'] for r in records]),
            'retries': stats([r['retries'] for r in records]),
            'scene_start_ms': stats([r['scene_start_ms'] for r in records]),
            'finish_reason_reported': sum(r['finish_reason'] is not None for r in records),
            'finish_reasons': dict(Counter(r['finish_reason'] for r in records if r['finish_reason'] is not None)),
            'truncation_reported': sum(r['truncated'] is not None for r in records),
        }
    cases = {}
    required = metadata['plan']['repetitions'] // 2 + 1
    for cid in metadata['plan']['case_ids']:
        selected = [p for p in pairs if p['case_id'] == cid]
        votes = Counter(p['winner'] for p in selected)
        cases[cid] = {'votes': dict(votes), 'winner': next((v for v in ('A', 'B') if votes[v] >= required),
                                                        'tie' if len(selected) >= required else 'unscored')}
    result = {'plan': metadata['plan'], 'model': metadata['config'],
              'case_input_hashes': metadata['case_input_hashes'],
              'versions': {v: {'prompt_hash': metadata['files'][f'{v}/prompt.md'],
                               'schema_hash': metadata['files'][f'{v}/schema.json']} for v in ('A', 'B')},
              'judging': {'judge': judge, 'date': date, 'mode': mode},
              'pair_votes': dict(Counter(p['winner'] for p in pairs)),
              'case_votes': dict(Counter(c['winner'] for c in cases.values())),
              'quality': {v: {d: stats([p['scores'][v][d] for p in pairs])
                              for d in (*DIMENSIONS, 'total')} for v in ('A', 'B')},
              'performance': performance, 'cases': cases, 'pairs': pairs,
              'excluded_pairs': mapping['excluded']}
    output_dir.mkdir(parents=True, exist_ok=False)
    write(output_dir / 'report.json', result)
    comparison = output_dir / 'comparison'
    comparison.mkdir()
    for variant in ('A', 'B'):
        for name in ('prompt.md', 'schema.json'):
            shutil.copyfile(run_dir / variant / name,
                            comparison / f'{Path(name).stem}_{variant}{Path(name).suffix}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('prepare', 'record', 'pack', 'report'):
        p = sub.add_parser(name)
        p.add_argument('--run-dir', type=Path, required=True)
        if name == 'prepare':
            p.add_argument('--service', default='opencode-go')
        if name == 'report':
            p.add_argument('--output-dir', type=Path, help='Default: <run-dir>/analysis; must remain inside local')
            p.add_argument('--judge', required=True)
            p.add_argument('--date', required=True)
            p.add_argument('--mode', choices=['fresh-web', 'human', 'codex-session'], required=True)
    args = parser.parse_args()
    if not args.run_dir.resolve().is_relative_to((BENCH / 'local').resolve()):
        parser.error('raw run directory must be inside benchmarks/narrative_quality/local/')
    if args.command == 'prepare':
        metadata = prepare(args.run_dir, service_config(args.service), args.service)
        print(json.dumps({'cases': metadata['plan']['case_ids'], 'calls': len(metadata['jobs']),
                          'model': metadata['config'], 'status': 'prepared; no LLM calls'}, ensure_ascii=False, indent=2))
    elif args.command == 'record':
        metadata = read(args.run_dir / 'run.json')
        record(args.run_dir, service_config(metadata['service']))
    elif args.command == 'pack':
        mapping = pack(args.run_dir)
        print(f'{len(mapping["pairs"])} blind pairs; {len(mapping["excluded"])} excluded')
    else:
        result = report(args.run_dir, args.output_dir, judge=args.judge, date=args.date, mode=args.mode)
        print(json.dumps(result['pair_votes'], ensure_ascii=False))


if __name__ == '__main__':
    main()
