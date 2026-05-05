## Problem Statement

Users need a minimal calculator capability to add two numbers reliably.

## Solution

Implement a small end-to-end slice for adding two inputs and returning their sum with validation for invalid input.

## User Stories

1. As a user, I want to enter two numbers and see their sum, so that I can quickly perform basic addition.
2. As a user, I want invalid input to be rejected with a clear message, so that I understand how to fix my input.

## Implementation Decisions

- Build one thin vertical slice for add-two-numbers.
- Reuse existing validation and testing style.
- Keep interface and behavior minimal.

## Testing Decisions

- Add happy path test for valid numeric input.
- Add failure path test for invalid/non-numeric input.

## Out of Scope

- Subtraction, multiplication, division.
- Calculator history and advanced UI behavior.

## Further Notes

- This PRD is intentionally minimal for workflow smoke-testing.
