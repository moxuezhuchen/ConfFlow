# Phase F owner exception — 2026-08-11

The repository owner authorized direct retirement of the optional
`confflow-agent` daemon because there are no active consumers requiring its
compatibility surface. This is an intentional breaking product decision, not a
compatibility-period success claim.

The `confflow-agent` console entry point, `confflow --agent` forwarding,
daemon implementation, deployment files, and dedicated tests are removed.
The production `confflow-control-worker`, fixture-only `confflow-fixture-agent`,
and core workflow state/protocol remain supported and are not part of this
retirement.

Existing user daemon state, queues, SQLite databases, logs, and systemd units
are not deleted by this repository change. Before deployment, an operator who
previously installed the daemon should stop it explicitly, for example:

```sh
systemctl --user disable --now confflow-agent
```

No release/tag/deployment is implied by this source change.
