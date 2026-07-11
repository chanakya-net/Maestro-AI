# Complexity-Band Model Routing Design

## Problem

Automatic worker routing currently derives candidates primarily from broad
complexity weights. This allows models outside the desired operating policy to
enter a band, including GPT-5.4 for complex and `holy-fuck` work. Model effort is
also stored as a single catalog value, so GPT-5.6 Sol cannot run at `high` for
medium-hard work and `xhigh` for complex work. Claude effort is not represented
or passed to Claude Code at all.

The routing policy must become explicit by effective complexity band while
preserving complexity scoring, subscription-aware distribution, role-specific
band escalation, availability filtering, and explicit operator overrides.

## Goals

- Enforce an exact automatic-routing model set for every effective complexity
  band and every worker role except `complexity`.
- Add Claude Sonnet 5 under its canonical model ID, `claude-sonnet-5`.
- Prevent GPT-5.4 from automatic complex and `holy-fuck` work.
- Route GPT-5.6 Sol with `high` effort for medium-hard work and `xhigh` effort
  for complex and `holy-fuck` work.
- Apply equivalent band-sensitive effort controls to Claude Sonnet 5 and Claude
  Opus 4.8.
- Continue distributing eligible work across Codex, Claude, and Gemini/Agy,
  using Codex, then Claude, then Agy as the preference order when routing debt
  and candidate fitness are otherwise equal.
- Prove with executable tests that numeric complexity calculation determines the
  effective band and that automatic selection stays inside that band's policy.

## Non-Goals

- Do not change the nine-dimension complexity scoring prompt or score ranges.
- Do not constrain the `complexity` worker with the new band allowlists.
- Do not remove explicit `FORCED_AGENT` or `FORCED_MODEL` overrides. Explicit
  operator intent remains higher precedence than automatic policy.
- Do not change retry exclusions, availability caches, sticky reviewers,
  subscription ledger persistence, or permanently blocked agents.
- Do not add provider API integrations; execution remains through the existing
  Codex, Claude Code, and Agy CLIs.

## Automatic Model Matrix

The router applies this matrix after calculating the role's effective routing
band. The list is exact for automatic routing; models not listed in a band are
not candidates for non-complexity roles.

| Effective band | Allowed models |
|---|---|
| `quite-easy` | `gpt-5.4`, `gpt-5.3-codex-spark`, `gpt-5.6-luna`, `claude-sonnet-5`, `claude-haiku-4-5`, and eligible Gemini models exposed by Agy |
| `easy` | `gpt-5.4`, `gpt-5.3-codex-spark`, `gpt-5.6-luna`, `claude-sonnet-5`, `claude-haiku-4-5`, and eligible Gemini models exposed by Agy |
| `medium` | `gpt-5.6-terra`, `gpt-5.3-codex-spark`, `claude-sonnet-5` |
| `medium-hard` | `gpt-5.5`, `gpt-5.6-sol`, `gpt-5.3-codex-spark`, `claude-sonnet-5` |
| `complex` | `gpt-5.6-sol`, `claude-opus-4-8` |
| `holy-fuck` | `gpt-5.6-sol`, `claude-opus-4-8` |

"Eligible Gemini models" means current `model_catalog` entries whose provider is
`google` and which are listed in the detected Agy agent's `known_models`. This
keeps the simple-work policy aligned with the registry as Gemini inventory
changes, without admitting non-Google Agy models.

GPT-5.5 changes from explicit-only to automatic eligibility in the
`medium-hard` band only. It remains unavailable to every other automatic band.

## Effective Band Rules

- `complexity`: retains the existing independent `1..6` weight-based candidate
  logic and does not use the new matrix.
- `impl`, `modify`, `artifact-recovery`, and `merge-recovery`: use the issue's
  base complexity band.
- `review`: retains the existing one-band increase, capped at `holy-fuck`, then
  applies the matrix.
- `plan`: retains the existing two-band increase, capped at `holy-fuck`, then
  applies the matrix.

For example, an implementation with score 21 maps to `medium` and may use only
Terra, Codex Spark, or Sonnet 5. Its reviewer routes at `medium-hard` and may use
only GPT-5.5, Sol, Codex Spark, or Sonnet 5.

## Effort Matrix

Effort is selected from the effective routing band, not merely from the model
ID. An empty entry means the runner does not inject an effort override.

| Model | `quite-easy` | `easy` | `medium` | `medium-hard` | `complex` | `holy-fuck` |
|---|---:|---:|---:|---:|---:|---:|
| `gpt-5.6-sol` | — | — | — | `high` | `xhigh` | `xhigh` |
| `claude-sonnet-5` | `low` | `medium` | `medium` | `high` | — | — |
| `claude-opus-4-8` | — | — | — | — | `xhigh` | `max` |
| `claude-haiku-4-5` | provider default | provider default | — | — | — | — |

GPT-5.6 supports `high` and `xhigh` reasoning effort. Claude Sonnet 5 and Claude
Opus 4.8 support provider-native effort levels; Claude Haiku 4.5 does not use
the new effort override.

## Registry Schema

`agent-registry.json` remains the source of routing policy. Add two declarative
structures under `model_routing`:

1. A non-complexity band allowlist. Each band contains explicit model IDs plus a
   provider selector for eligible Google models in the two simple bands.
2. A model-by-band effort map containing only models that require an explicit
   override.

The registry also adds `claude-sonnet-5` to `model_catalog` and to the Claude
agent's `known_models`. Its catalog metadata uses provider `anthropic`, a 1M
context window, and routing metadata matching the matrix. Existing broad
weights remain available for complexity scoring and as candidate-fit metadata;
the exact allowlist wins for non-complexity automatic routing.

Every non-complexity role uses Codex, Claude, then Agy as its agent preference
order. This replaces the existing Claude-first preference for review and plan
workers. Non-complexity roles also share the following effective-band targets so
the accumulated usage ledger and the preference order express the same policy:

| Effective band | Codex | Claude | Agy |
|---|---:|---:|---:|
| `quite-easy` | 55% | 35% | 10% |
| `easy` | 55% | 40% | 5% |
| `medium` | 70% | 30% | 0% |
| `medium-hard` | 70% | 30% | 0% |
| `complex` | 70% | 30% | 0% |
| `holy-fuck` | 60% | 40% | 0% |

Only simple bands distribute to Gemini/Agy because the approved medium and
higher model sets contain no Gemini models. Preference order remains a
deterministic tie-breaker after usage-share debt; it does not disable ledger
balancing.

## Router and Runner Data Flow

1. `run-with-it-router.py` converts a numeric score to a base band using the
   existing `score_to_weight` table.
2. It applies the existing role transformation to produce `routing_level`.
3. For `complexity`, it follows the existing candidate path unchanged. For every
   other role, it builds candidates from the exact band allowlist.
4. It applies detected-agent compatibility, allow/deny lists, availability,
   retry exclusions, provider routing, and forced overrides.
5. It ranks remaining pairs through the existing usage-share debt algorithm.
6. It resolves the selected model's effort for `routing_level` and returns that
   value in route JSON and the recorded decision.
7. The Sub-Coordinator passes the selected effort to the platform dispatcher.
8. The dispatcher passes a generic model-effort override to `run-agent`.
9. `run-agent` renders it for the selected provider:
   - Codex: `-c model_reasoning_effort=<level>`
   - Claude Code: `--effort <level>` or the equivalent
     `CLAUDE_CODE_EFFORT_LEVEL=<level>` session override
   - Agy: no effort flag

Direct `run-agent` calls without a router-provided effort retain catalog/default
behavior. A route-provided value has precedence because it carries the effective
complexity band that the runner cannot independently reconstruct.

## Selection and Failure Behavior

- Explicit forced models remain valid even when outside the automatic matrix.
- A forced model must still exist in the catalog, be supported by a detected
  permitted agent, and survive availability exclusions.
- If the automatic band contains no live compatible pair, routing fails with the
  existing filter diagnostics. It must not expand into a forbidden model band.
- Retry exclusions may reduce the allowed set but may not add models.
- Review's implementation-model exclusion and sticky-reviewer preference remain
  subordinate to the exact allowed set.
- Gemini models route only through Agy under the existing provider rule.

## Platform Parity

Bash and PowerShell implementations must carry identical model and effort
values through router output, Sub-Coordinator dispatch, dispatcher state/status,
and runner invocation. Dry-run output on both platforms must expose enough data
to verify the selected effort without launching a provider CLI.

## Testing

Tests will cover:

1. The registry contains Sonnet 5, the exact automatic band matrix, and the
   effort matrix.
2. GPT-5.4 is absent from complex and `holy-fuck` automatic candidates.
3. Numeric scores at every boundary map to the expected band before selection.
4. Every automatically selected non-complexity model belongs to the effective
   band's allowlist.
5. Review still routes one band higher and plan still routes two bands higher.
6. Sol emits `high` at medium-hard and `xhigh` at complex/`holy-fuck`.
7. Sonnet 5 emits `low`, `medium`, or `high` according to its effective band.
8. Opus 4.8 emits `xhigh` for complex and `max` for `holy-fuck`.
9. Bash and PowerShell runners translate the generic effort into the correct
   provider-specific invocation.
10. Explicit forced-model overrides continue to work outside the automatic
    matrix.
11. Existing availability, exclusion, ledger, and complexity-worker tests remain
    green.
12. Every non-complexity role uses the Codex, Claude, Agy preference order and
    the effective-band distribution targets, while complexity scoring retains
    its existing independent targets.

## Documentation and Installation

Update the README, `run-with-it` skill, Sub-Coordinator prompt, and registry
contract comments to describe the exact band policy and effort propagation.
Repository assets remain the source of truth. After verification, synchronize
the normal installed asset copy using the project's existing installation flow
so future local runs do not continue using stale routing files.
