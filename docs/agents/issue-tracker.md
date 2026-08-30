# Issue tracker: Local Markdown

Issues and feature specs for this repository live as Markdown files under `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The feature spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are stored one per file under `.scratch/<feature-slug>/issues/`
- Issue filenames use `<NN>-<slug>.md`, numbered from `01`
- Comments and conversation history are appended under a `## Comments` heading
- Do not combine all implementation tickets into one file

## Publishing

When a skill says “publish to the issue tracker,” create the appropriate file under `.scratch/<feature-slug>/`, creating its directories when necessary.

## Fetching

When a skill says “fetch the relevant ticket,” read the referenced Markdown file. The user will normally provide its path or issue number.

## Wayfinding operations

- Map: `.scratch/<effort>/map.md`
- Child ticket: `.scratch/<effort>/issues/NN-<slug>.md`
- Ticket type: a `Type:` line containing `research`, `prototype`, `grilling`, or `task`
- Ticket state: a `Status:` line containing `claimed` or `resolved`
- Blocking dependencies: a `Blocked by: NN, NN` line
- Frontier: the first open, unblocked, unclaimed ticket by number
- Claim: set `Status: claimed` before starting work
- Resolve: append the result under `## Answer`, set `Status: resolved`, and add a context pointer to the map’s Decisions-so-far section
