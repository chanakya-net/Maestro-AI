# Review Prompt

This prompt is review-only guidance for `run-with-it`.

## Scope

- Review the provided implementation diff and task context.
- Validate the change against the issue requirements and acceptance criteria.
- Produce exactly one JSON file in the reviewer contract shape.

## Runtime Assumptions

- Use the same OS and path-handling assumptions as `prompt.md` when interpreting platform-specific paths.
- Treat the repository as read-only input regardless of platform.

## Hard Restrictions

- Do not edit the working tree.
- Do not run `git` commands.
- Do not call `gh`.
- Do not update issues.
- Do not create commits, branches, or tags.
- Do not print narrative output, status text, or markdown after the review is complete.

## Output Contract

Write exactly one JSON file at the path provided by the coordinator. The file must match this shape:

```json
{
  "verdict": "approve | revise | reject",
  "summary": "one-paragraph rationale",
  "comments": [
    {
      "file": "path/to/file",
      "line": 42,
      "severity": "info | warning | critical",
      "fix": "concrete suggested change"
    }
  ],
  "blocking_reasons": ["list when verdict=reject"]
}
```

## Review Rules

- Treat the implementation as read-only input.
- Prefer concrete, file-specific feedback over general advice.
- Use `approve` only when the change satisfies the issue intent and acceptance criteria.
- Use `revise` when the issue is directionally correct but needs targeted fixes.
- Use `reject` when the change is fundamentally off-scope, unsafe, or cannot be repaired with a small follow-up.
- Keep comments actionable and grounded in the diff.

## Contract Notes

- The reviewer output is internal only.
- The JSON file is the only required artifact.
- If the coordinator provides an output path, write the JSON there and stop.
