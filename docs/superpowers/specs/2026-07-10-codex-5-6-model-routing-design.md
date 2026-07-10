# Codex GPT-5.6 Model Routing Design

## Goal

Add Codex support for GPT-5.6 Luna, Terra, and Sol, route each model to the intended complexity bands, and make `high` reasoning an invariant of every invocation of those three models. Keep GPT-5.5 available only through an explicit model request.

## Verified Codex Invocation

The public Codex model documentation identifies the canonical CLI model IDs as:

- `gpt-5.6-luna`
- `gpt-5.6-terra`
- `gpt-5.6-sol`

Codex accepts per-invocation configuration overrides with `-c`, and `model_reasoning_effort` supports `high`. Each canonical model was verified successfully against the installed Codex CLI with this non-interactive form:

```bash
codex exec -m <canonical-model-id> \
  -c model_reasoning_effort=high \
  --ephemeral --sandbox read-only \
  "Reply with exactly OK. Do not use tools."
```

Although short names such as `sol` work in the interactive model selector, the repository uses `codex exec`. Live non-interactive checks showed that `luna`, `terra`, and `sol` were passed literally and rejected by the ChatGPT backend. The registry must therefore use the canonical `gpt-5.6-*` IDs.

Sources:

- [Codex models](https://developers.openai.com/codex/models)
- [Codex configuration reference](https://developers.openai.com/codex/config-reference)
- [Codex CLI reference](https://developers.openai.com/codex/cli/reference)

## Registry Model Metadata

Add the following entries to `model_catalog` and to the Codex agent's `known_models` list:

| Model | Ability | Weight | Minimum band | Normal eligible bands | Context window |
|---|---|---:|---|---|---:|
| `gpt-5.6-luna` | balanced | 3 | `easy` | easy | 372,000 |
| `gpt-5.6-terra` | advanced | 5 | `medium` | medium | 372,000 |
| `gpt-5.6-sol` | frontier | 7 | `medium-hard` | medium-hard, complex | 372,000 |

The context-window value comes from the installed Codex model catalog. Each entry also declares `reasoning_effort: "high"`. This is executable model metadata, not documentation-only metadata.

Sol is added to `model_routing.band_required_models["holy-fuck"]` so it remains eligible above the normal weight-7 ranges. Its `min_band` prevents it from entering lower bands.

## Atomic High-Reasoning Invariant

Both runner implementations resolve `reasoning_effort` for the selected model and render it as two command arguments:

```text
-c
model_reasoning_effort=high
```

The Codex invocation template gains a model-settings placeholder after `{{extra_args}}` and before `{{prompt}}`. This ordering makes the registry-controlled value the last reasoning-effort override and prevents caller-supplied extra arguments from accidentally downgrading one of the new models.

Models without `reasoning_effort` metadata keep their current invocation behavior. The implementation must preserve Bash and PowerShell parity.

## GPT-5.5 Explicit-Only Behavior

GPT-5.5 remains in `model_catalog` and in the Codex `known_models` list, but gains `explicit_only: true`.

Automatic candidate construction skips `explicit_only` models. Forced model selection returns the requested catalog entry before applying that automatic-routing filter, so these explicit paths remain valid:

- Router `--forced-model gpt-5.5`
- Runner `--model gpt-5.5`
- Explicit `SUB_COORD_MODEL=gpt-5.5`

Remove GPT-5.5 from the complex and `holy-fuck` required-model lists. Change the implicit Sub-Coordinator default in the Bash runner, PowerShell runner, skill documentation, and README from GPT-5.5 to `gpt-5.6-sol`; otherwise an ordinary run would still select GPT-5.5 without an explicit request.

The maintained router at `assets/run-with-it-router.py` receives the explicit-only filtering behavior. No packaged duplicate exists in the current tree.

## Data Flow

1. The router receives the task role and complexity band.
2. Automatic routing filters out catalog entries marked `explicit_only`.
3. Complexity weight, `min_band`, and required-model pins make Luna eligible for easy, Terra for medium, and Sol for medium-hard and higher work.
4. The selected canonical model ID reaches `run-agent.sh` or `run-agent.ps1`.
5. The runner reads the selected model's `reasoning_effort` metadata.
6. The runner constructs one Codex command containing both the canonical `--model` value and the final `model_reasoning_effort=high` override.
7. Telemetry continues reporting the canonical model ID.

An explicit GPT-5.5 request bypasses only the automatic-routing exclusion. Agent availability, deny lists, and other existing compatibility checks continue to apply.

## Error Handling and Compatibility

- Unknown forced models continue to fail with the existing catalog validation error.
- An unavailable or denied GPT-5.5 route remains unavailable even when requested explicitly.
- Short GPT-5.6 aliases are not registered because they fail in the repository's non-interactive execution path.
- Existing Codex models do not inherit `high` reasoning unless they already receive it from an explicit caller override.
- Existing external `AGENT_EXTRA_ARGS` behavior remains unchanged for settings unrelated to the model invariant.

## Test Strategy

Follow a red-green-refactor sequence.

1. Extend registry contract tests to require the three canonical models, their context windows, weights, minimum bands, and `high` reasoning metadata.
2. Add router tests showing:
   - Luna is eligible at easy and excluded from lower and higher normal bands.
   - Terra is eligible at medium and excluded from other normal bands.
   - Sol is eligible from medium-hard through `holy-fuck`.
   - GPT-5.5 never appears in automatic routing.
   - `--forced-model gpt-5.5` still succeeds.
3. Add Bash dry-run tests for all three new models that assert canonical `--model` arguments and the atomic `-c model_reasoning_effort=high` pair.
4. Add a precedence test with caller-supplied medium effort and assert that the registry-controlled high effort occurs later in the generated command.
5. Add equivalent PowerShell contract or execution coverage.
6. Update Sub-Coordinator default tests and documentation contracts from GPT-5.5 to Sol.
7. Run the focused runner/router suites, then the complete shell test suite.

## Out of Scope

- Removing GPT-5.5 from the catalog or direct runner use.
- Changing routing percentages between Codex, Claude, and Agy.
- Applying `high` reasoning to older Codex models.
- Registering interactive-only short model aliases.
- Changing API pricing metadata or subscription accounting.
