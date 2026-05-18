import assert from "node:assert/strict";
import test from "node:test";
import { extractDeviceCodes, extractUrls, sanitizeTerminalText, redactSecrets, toServerSentEvent } from "./sessionManager";

test("redactSecrets hides common OAuth and API token shapes", () => {
  const text = [
    "Authorization: Bearer abc.def.ghi",
    "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz",
    "ANTHROPIC_AUTH_TOKEN=super-secret-value",
    "safe text remains"
  ].join("\n");

  const redacted = redactSecrets(text);

  assert.match(redacted, /Bearer \[REDACTED\]/);
  assert.match(redacted, /OPENAI_API_KEY=\[REDACTED\]/);
  assert.match(redacted, /ANTHROPIC_AUTH_TOKEN=\[REDACTED\]/);
  assert.match(redacted, /safe text remains/);
  assert.doesNotMatch(redacted, /super-secret-value/);
});

test("toServerSentEvent serializes named events as SSE frames", () => {
  assert.equal(
    toServerSentEvent({ type: "stdout", text: "hello" }),
    'event: stdout\ndata: {"type":"stdout","text":"hello"}\n\n'
  );
});

test("extractDeviceCodes finds Codex device authorization codes", () => {
  const text = [
    "Follow these steps to sign in with ChatGPT using device code authorization:",
    "2. Enter this one-time code (expires in 15 minutes)",
    "   ABCD-EF123"
  ].join("\n");

  assert.deepEqual(extractDeviceCodes(text), ["ABCD-EF123"]);
});

test("sanitizeTerminalText removes ANSI escapes and raw control bytes from login output", () => {
  const text = "\u0001\u0001�\r\nOpen \u001b[94mhttps://auth.openai.com/codex/device\u001b[0m\r\n";

  assert.equal(sanitizeTerminalText(text), "\r\nOpen https://auth.openai.com/codex/device\r\n");
});

test("extractUrls ignores terminal color resets after auth links", () => {
  const text = "Open \u001b[94mhttps://auth.openai.com/codex/device\u001b[0m";

  assert.deepEqual(extractUrls(sanitizeTerminalText(text)), ["https://auth.openai.com/codex/device"]);
});

test("extractUrls trims sentence punctuation from documentation links", () => {
  const text = "See https://developers.openai.com/codex/concepts/sandboxing#prerequisites.";

  assert.deepEqual(extractUrls(text), [
    "https://developers.openai.com/codex/concepts/sandboxing#prerequisites"
  ]);
});
