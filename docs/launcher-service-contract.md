# Launcher Service Contract

`csctl loop-status` is the read-only command surface for observing the AutomationPlus loop as a bounded controllable service. It does not start, stop, restart, or mutate loop state.

## Trust Boundary

AutomationPlus trusts the following observation inputs, in this order:

1. Live tmux session metadata for the configured session name, currently `automationplus-loop`.
2. The loop health snapshot produced by `automationplus.health_mirror`, normally at `.codex-supervisor/health/loop-health.json` in the workspace.
3. Optional launcher metadata at `.codex-supervisor/launcher/loop-service.json`.
4. Supervisor runtime state from `.local/state.json`, `.codex-supervisor/turn-in-progress.json`, and `.codex-supervisor/replay/decision-cycle-snapshot.json`.

If live tmux observation fails, `csctl loop-status` reports `status=unknown` with an `observationError` object instead of making control decisions.

## Launcher Responsibilities

The launcher owns process and session lifecycle.

- Create and remove the tmux session named `automationplus-loop`, or another explicitly configured session name.
- Own the launcher PID and any session leader PID.
- Publish launcher metadata atomically to `.codex-supervisor/launcher/loop-service.json` when available.
- Avoid mutating supervisor state files as part of status observation.

The supervisor does not claim ownership of the launcher PID or the tmux session. It is only an observed workload inside the launcher-controlled service boundary.

## Optional Launcher Metadata

When present, `.codex-supervisor/launcher/loop-service.json` should be a JSON object. The current status collector reads it opportunistically and ignores malformed content.

Recommended fields:

- `state`: launcher view of the service state, such as `running`, `stopped`, or `unknown`
- `pid`: launcher-owned PID for the service controller
- `startedAt`: ISO-8601 timestamp for the current launcher-owned run
- `sessionName`: tmux session name when the launcher is using tmux
- `workspaceRoot`: workspace path the launcher believes it is serving
- `supervisorRoot`: supervisor root path the launcher believes it is serving

## `csctl loop-status` Envelope

The command returns JSON with these top-level sections:

- `status`: one of `healthy`, `degraded`, `off`, or `unknown`
- `runtime`: live loop runtime details, including session and pane metadata
- `supervisor`: mirrored supervisor state and decision-cycle context
- `launcher`: contract metadata, runtime discovery paths, and optional launcher service metadata
- `drift`: agreement signals between runtime, supervisor, and decision-cycle snapshots

Current status semantics:

- `healthy`: loop runtime is running, pane is alive, and drift checks do not show disagreement
- `degraded`: loop runtime is running but pane is dead or drift checks disagree
- `off`: loop runtime is explicitly absent
- `unknown`: runtime could not be observed reliably

This contract is intentionally limited to observation so later restart and stop automation can target a stable, explicit boundary without coupling to ad hoc process discovery.
