---
name: tdd-implementation
description: Test-driven development with red-green-refactor loop. Use when user wants to build features or fix bugs using TDD, asks for test-first development, or wants stronger integration tests.
---

# Test-Driven Development

Use a strict red-green-refactor cycle with thin vertical slices.

## Philosophy

Core principle: test behavior through public interfaces, not implementation details.

Good tests:

- Validate end-to-end behavior through public APIs
- Read like specifications of capability
- Survive internal refactors

Bad tests:

- Mock internal collaborators excessively
- Assert private methods or internal call order
- Break when internals change but behavior stays the same

## Anti-Pattern: Horizontal Slices

Do not write all tests first and all implementation later.

Wrong (horizontal):

RED: test1, test2, test3
GREEN: impl1, impl2, impl3

Right (vertical tracer bullets):

RED->GREEN: test1->impl1
RED->GREEN: test2->impl2
RED->GREEN: test3->impl3

## Workflow

### 1. Plan with user

Before writing code:

- Confirm required public interface changes
- Confirm priority behaviors to test
- Identify deep modules (small surface, rich internals)
- Identify architecture constraints and ADRs in scope
- Get approval on test plan

Ask:

- What should the public interface look like?
- Which behaviors matter most?

### 2. First tracer bullet

Write one test for one behavior:

RED: write test, confirm it fails.
GREEN: write minimal code, confirm it passes.

As soon as the happy-path test is green, add the matching negative-path test for the same behavior (invalid input, rejected state, permission failure, boundary violation, or error path).

### 3. Incremental loop

For each remaining behavior:

RED: write next failing test.
GREEN: add only enough code to pass.

Rules:

- One test at a time
- Cover both positive and negative paths for each behavior before moving on
- No speculative features
- Keep assertions on observable behavior only

### 4. Refactor only on green

After tests pass:

- Remove duplication
- Deepen modules behind stable interfaces
- Improve naming and readability
- Re-run tests after each refactor step

Never refactor while red.

## Tech-Stack Alignment Rules

When implementing each slice:

- Reuse existing test framework and assertion style in the repo
- Reuse current package ecosystem (for example npm, NuGet, pip, Maven, Gradle)
- Follow current architecture and module boundaries
- Add new dependencies only with explicit justification

## Per-Cycle Checklist

- [ ] Test describes behavior, not implementation
- [ ] Test uses public interface only
- [ ] Test would survive internal refactor
- [ ] Code is minimal for current test
- [ ] Positive case is covered for this behavior
- [ ] Negative case is covered for this behavior
- [ ] No speculative or unrelated features added
