# Sub-Coordinator and Worker Routing Separation

## Problem

`SUB_COORD_AGENT` and `SUB_COORD_MODEL` select the fixed runtime used for every Sub-Coordinator. The generated issue context currently exposes that runtime as `AGENT` and `MODEL`. The Sub-Coordinator interprets those names as child-worker routing overrides and forwards them to the router as `FORCED_AGENT` and `FORCED_MODEL`.

Consequently, setting the Sub-Coordinator default to `gpt-5.6-sol` also forces complexity, implementation, and modification workers onto Sol. Complexity bands and usage distribution are bypassed even for easy work.

## Intended Contract

- `SUB_COORD_AGENT` and `SUB_COORD_MODEL` select only the Sub-Coordinator runtime.
- `FORCED_AGENT` and `FORCED_MODEL` are the canonical child-worker routing overrides.
- Top-level `AGENT` and `MODEL` remain deprecated compatibility aliases for explicit user-supplied worker overrides. They are translated at the orchestration boundary and must never be populated from the Sub-Coordinator runtime.
- Without an explicit worker override, every child role is selected by the router using complexity, role, availability, exclusions, and usage distribution.

## Design

### Context generation

Main Orchestrator instructions will distinguish Sub-Coordinator runtime fields from child-worker override fields. Generated issue contexts must not copy `SUB_COORD_AGENT` or `SUB_COORD_MODEL` into `AGENT` or `MODEL`.

When the user explicitly supplies a worker override, the issue context will contain `FORCED_AGENT` and/or `FORCED_MODEL`. Deprecated top-level `AGENT` and `MODEL` inputs will be normalized to these canonical names before context generation.

### Sub-Coordinator routing

The Sub-Coordinator will pass only `FORCED_AGENT` and `FORCED_MODEL` to `run-with-it-router.py`. Its own process runtime (`AGENT` and `MODEL`, populated by the dispatcher) is not routing policy and must not be promoted to forced worker values.

The router itself remains unchanged: forced values continue to have highest precedence when explicitly supplied.

### Platform parity

Bash and PowerShell pool/dispatch documentation and tests will enforce the same separation. Sol remains the default Sub-Coordinator model on both platforms.

## Compatibility

Existing callers that explicitly set top-level `AGENT` or `MODEL` retain worker-override behavior through boundary normalization. Internal dispatcher-provided `AGENT` and `MODEL` are not treated as user overrides.

The compatibility aliases will be documented as deprecated to prevent new integrations from depending on ambiguous names.

## Testing

Regression coverage will prove:

1. The pool dispatches Sub-Coordinators with `gpt-5.6-sol` by default.
2. Generated Sub-Coordinator context does not turn that runtime into `FORCED_MODEL`.
3. An easy Codex-only implementation route without an explicit override selects an eligible lightweight model such as Luna or Mini, not Sol.
4. An explicit `FORCED_MODEL=gpt-5.6-sol` still selects Sol and reports `forced-model` or `forced-agent-and-model`.
5. Bash and PowerShell contracts use the same canonical override names.

## Scope

This change does not alter model weights, complexity bands, usage targets, Sub-Coordinator defaults, or recovery policy. It only prevents coordinator runtime selection from leaking into child-worker routing.
