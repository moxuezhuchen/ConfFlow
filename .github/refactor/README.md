# Refactor program state

GitHub is the persistent state centre; the WSL OpenCode runner is a
stateless executor. Each round re-reads this directory, the linked Issue,
the branch, and commit history — never an old OpenCode session.

## Layout

- `state.schema.json` — JSON Schema for the state file. Validate with:
  `python -c "import json,yaml,jsonschema; jsonschema.validate(yaml.safe_load(open('.github/refactor/state.yaml')), json.load(open('.github/refactor/state.schema.json')))"`
  (plain `jsonschema -i` CLI cannot read YAML; use the command above).
- `example-state.yaml` — example only, not a progress claim. Copy to
  `state.yaml` when a real program starts.
- `README.md` — this file (completion + multi-round rules).

## Phase lifecycle

```text
pending → ready → running → review → done
                    ↕           ↕
                 blocked      failed
```

Status is advanced ONLY by humans (or a future reviewer gate):

- The executor sets at most `running` (on start) and leaves the phase at
  `review` (on push) — or `blocked`/`failed` with the blocker in `notes`.
- The executor must never write `done` for itself.
- A phase is formally complete only when ALL hold:
  code pushed + requested tests pass + GitHub CI green +
  acceptance criteria satisfied + no blocking review.
- Then a reviewer flips the entry to `done` and opens the next phase.

## Multi-round repair

A phase may take many rounds: `P3 round 1 → review → repair → P3 round 2 …`.
Every round is a NEW OpenCode session (`workflow_dispatch` again with the
same `phase` + `target_branch`); the existing branch is continued with
`git pull --ff-only`, never rebased or overwritten. Increment `rounds`,
update `last_commit`/`model`, and append reviewer notes after each round.
