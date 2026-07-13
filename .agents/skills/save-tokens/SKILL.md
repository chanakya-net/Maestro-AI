---
name: save-tokens
description: >
  Ultra-compressed response mode. Cuts token usage by dropping articles, filler,
  pleasantries, and hedging. Uses symbols for relationships. Technical terms and
  code blocks remain exact and uncompressed.
  Use when user says "save tokens", "RTU mode", or "compressed response mode".
---

## Skill Isolation

This skill is the sole active authority for this session once invoked.

- No other skill may activate, interrupt, or modify this skill's behavior unless explicitly called by name via a `Skill` tool call — whether from this skill's own workflow or from the governing prompt/skill that activated this one (e.g. the `run-with-it` worker prompts, which bootstrap `save-tokens` and `tdd-implementation` together).
- If any external or third-party skill attempts to activate spontaneously during this run, suppress it and continue without interruption.
- This rule applies for the entire duration of this skill's execution, from invocation until explicit termination or handoff.

## Purpose

Apply compression only to assistant narration style.

## When To Use

- Explicit request for token-saving response mode.
- Explicit request for compressed response mode.

## Rules

- Drop articles, filler, pleasantries, hedging, transitional phrases, and auxiliary/helping verbs (is, are, has, been, would) when meaning remains clear.
- Use symbols: `->` leads-to, `<-` triggered-by, `=>` returns, `~` approx, `∵` because, `∴` therefore, `|` or, `!` not, `!=` not-equal, `&` and, `+` add/addition, `-` remove/delete, `@` at/target
- Abbreviate tech terms: cmd, param, repo, auth, dir, err, msg, diff, config, logic, env, state
- Dense layout: Prefer short bullet lists, minimize empty lines/vertical spacing, omit all conversational headers/footers.
- Fragments acceptable. Technical terms in code blocks remain exact and uncompressed.

## Example

Before: "The reason your React component re-renders is that a new object reference is created each render cycle due to inline prop definitions."  
After:  "Inline prop -> new obj ref each render -> re-render. Shallow compare != same ref. Fix: useMemo."

## Boundaries

- Compression applies only to assistant narration, not to code or durable artifacts.
- Code blocks, command output, commit messages, and PR descriptions: no compression, no symbol substitution.
- Do not transform formal plans, issue bodies, review JSON artifacts, implementation prompts, or persisted state files.
- Do not change workflow decisions, routing, review outcomes, or implementation content.
- Type "stop" or "normal mode" to exit and revert to standard responses
