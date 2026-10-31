# Bundled story starts

The three JSON files are the reviewed authoring sources for traditional fantasy,
wuxia and xianxia. Each includes public selection copy, `InitConfig`, public
campaign conventions, Opening Foundation, initial world settings and a pending
scene with its closing question and action directions.

`config` holds world style, protagonist background, learned skills and narrative
preferences. Temporary circumstances belong to `foundation`; `opening_state`
provides the authored clock and current player status. World descriptions and
setting facts describe the world, while the opening scene owns the narrated
events. The compiler saves the authored state in both CurrentState and the
pending opening before export.

To rebuild after editing content or changing the current save format:

```sh
PYTHONPATH=. .venv/bin/python scripts/build_opening_presets.py
```

To rebuild one preset while preserving the other archives and catalog entries,
add `--preset fantasy` (or `wuxia`, `xianxia`) to the command.

The offline authoring adapter supplies the saved agent outputs to the normal
initialization graph and its validators. That graph commits CoreMemory, persists
initial state and settings, and creates the recoverable pending opening through
the normal stores. The script then uses the normal ZIP export. It never confirms
the opening, contacts a model, or reads an owner's saved campaign. Runtime paths
are isolated in a temporary application directory before runtime modules load.

Commit both the sources and rebuilt `app/opening_presets/catalog.json` and ZIPs.
Only that directory is bundled; authoring tools and source JSON are not needed
at runtime. The catalog contains public preview text, not backstage material.
Selection imports a new save identity and opens it through the existing journey
entry flow. The source archives remain read-only.

Relevant checks live in `test_saves.py`, `test_turn_confirmation.py`,
`test_server_security.py` and `test_packaging.py`. They use the actual bundled
archives to cover complete initialization, copy isolation, pending edits,
confirmation/restart/Undo/Redo, public API identity and packaged resources.
