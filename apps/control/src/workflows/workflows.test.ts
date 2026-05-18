import assert from "node:assert/strict";
import test from "node:test";
import { buildWorkflowCommand, getWorkflowTitle, isWorkflowId } from "./index";

test("smoke workflow dispatch builds the runner command", () => {
  const command = buildWorkflowCommand("smoke", {
    agent: "codex",
    model: "gpt-5.3-codex-spark"
  });

  assert.equal(command[0], "bash");
  assert.equal(command[1], "-lc");
  assert.match(command[2], /TOOL_OK/);
  assert.match(command[2], /--agent 'codex'/);
  assert.match(command[2], /--model 'gpt-5\.3-codex-spark'/);
});

test("workflow titles are suitable for streamed sessions", () => {
  assert.equal(getWorkflowTitle("smoke", "codex"), "Smoke test Codex");
});

test("workflow identifiers are constrained to implemented workflows", () => {
  assert.equal(isWorkflowId("smoke"), true);
  assert.equal(isWorkflowId("break-req"), false);
});
