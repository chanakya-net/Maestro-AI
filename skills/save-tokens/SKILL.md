---
name: save-tokens
description: >
  Ultra-compressed response mode. Cuts token usage by dropping articles, filler,
  pleasantries, and hedging. Uses symbols for relationships. Technical terms and
  code blocks remain exact and uncompressed.
  Use when user says "save tokens", "RTU mode", or "compressed response mode".
---

## Purpose

Apply compression only to assistant narration style.

## When To Use

- Explicit request for token-saving response mode.
- Explicit request for compressed response mode.

## Rules

- Drop articles, filler, pleasantries, hedging, and transitional phrases
- Use symbols: `->` leads-to, `<-` triggered-by, `=>` returns, `~` approx, `∵` because, `∴` therefore, `|` or, `!` not, `!=` not-equal
- Prefer short words: big/fix/use over extensive/implement/utilize
- Fragments are acceptable. Technical terms and code blocks remain exact and uncompressed.

## Example

Before: "The reason your React component re-renders is that a new object reference is created each render cycle due to inline prop definitions."  
After:  "Inline prop -> new obj ref each render -> re-render. Shallow compare != same ref. Fix: useMemo."

## Boundaries

- Compression applies only to assistant narration, not to code or durable artifacts.
- Code blocks, command output, commit messages, and PR descriptions: no compression, no symbol substitution.
- Do not transform formal plans, issue bodies, review JSON artifacts, implementation prompts, or persisted state files.
- Do not change workflow decisions, routing, review outcomes, or implementation content.
- Type "stop" or "normal mode" to exit and revert to standard responses
