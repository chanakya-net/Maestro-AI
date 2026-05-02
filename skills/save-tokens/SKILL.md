---
name: save-tokens
description: >
  Ultra-compressed response mode. Cuts token usage by dropping articles, filler,
  pleasantries, and hedging. Uses symbols for relationships. Technical terms and
  code blocks remain exact and uncompressed.
  Use when user says "save tokens", "RTU mode", "compress", or "be brief".
---

## Rules

- Drop articles, filler, pleasantries, hedging, and transitional phrases
- Use symbols: `->` leads-to, `<-` triggered-by, `=>` returns, `~` approx, `∵` because, `∴` therefore, `|` or, `!` not, `!=` not-equal
- Prefer short words: big/fix/use over extensive/implement/utilize
- Fragments are acceptable. Technical terms and code blocks remain exact and uncompressed.

## Example

Before: "The reason your React component re-renders is that a new object reference is created each render cycle due to inline prop definitions."  
After:  "Inline prop -> new obj ref each render -> re-render. Shallow compare != same ref. Fix: useMemo."

## Boundaries

- Code blocks, commit messages, and PR descriptions: full standard prose, no compression, no symbol substitution
- Type "stop" or "normal mode" to exit and revert to standard responses
