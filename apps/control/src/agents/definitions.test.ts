import assert from "node:assert/strict";
import test from "node:test";
import { getAgentDefinition, listInstallableAgents } from "./definitions";

test("Claude installs into the runner home and uses container-native browser login", () => {
  const claude = getAgentDefinition("claude");

  assert.deepEqual(claude.installCommand, [
    "bash",
    "-lc",
    "export NPM_CONFIG_PREFIX=\"$HOME/.local\" NPM_CONFIG_CACHE=\"$HOME/.npm\" PATH=\"$HOME/.local/bin:$PATH\"; npm install -g @anthropic-ai/claude-code && /opt/ai-skills/install.sh --only claude --no-color"
  ]);
  assert.deepEqual(claude.loginCommand, ["claude", "auth", "login", "--claudeai"]);
  assert.deepEqual(claude.statusCommand, ["claude", "auth", "status", "--text"]);
});

test("Codex uses device auth because runner login happens inside Docker", () => {
  const codex = getAgentDefinition("codex");

  assert.deepEqual(codex.installCommand, [
    "bash",
    "-lc",
    "export NPM_CONFIG_PREFIX=\"$HOME/.local\" NPM_CONFIG_CACHE=\"$HOME/.npm\" PATH=\"$HOME/.local/bin:$PATH\"; npm install -g @openai/codex && /opt/ai-skills/install.sh --only codex --no-color"
  ]);
  assert.deepEqual(codex.loginCommand, ["codex", "login", "--device-auth"]);
  assert.deepEqual(codex.statusCommand, ["codex", "login", "status"]);
  assert.match(codex.loginNote ?? "", /device code authorization/i);
  assert.equal(codex.authSettingsUrl, "https://chatgpt.com/#settings/Security");
});

test("GitHub Copilot uses the GitHub-native Copilot CLI", () => {
  const copilot = getAgentDefinition("github-copilot");

  assert.deepEqual(copilot.installCommand, [
    "bash",
    "-lc",
    "export NPM_CONFIG_PREFIX=\"$HOME/.local\" NPM_CONFIG_CACHE=\"$HOME/.npm\" PATH=\"$HOME/.local/bin:$PATH\"; npm install -g @github/copilot && /opt/ai-skills/install.sh --only copilot --no-color"
  ]);
  assert.deepEqual(copilot.loginCommand, ["copilot", "login"]);
  assert.deepEqual(copilot.statusCommand, [
    "bash",
    "-lc",
    "test -n \"$COPILOT_GITHUB_TOKEN\" || test -n \"$GH_TOKEN\" || test -n \"$GITHUB_TOKEN\" || test -s \"$HOME/.copilot/config.json\" || test -s \"$HOME/.copilot/settings.json\" || gh auth token >/dev/null 2>&1"
  ]);
  assert.equal(copilot.experimental, undefined);
});

test("Gemini installs the Google Gemini CLI and starts interactive auth", () => {
  const gemini = getAgentDefinition("gemini");

  assert.deepEqual(gemini.installCommand, [
    "bash",
    "-lc",
    "export NPM_CONFIG_PREFIX=\"$HOME/.local\" NPM_CONFIG_CACHE=\"$HOME/.npm\" PATH=\"$HOME/.local/bin:$PATH\"; npm install -g @google/gemini-cli && /opt/ai-skills/install.sh --only gemini --no-color"
  ]);
  assert.deepEqual(gemini.loginCommand, ["gemini"]);
  assert.match(gemini.loginNote ?? "", /GEMINI_API_KEY/);
});

test("agent list only includes definitions that the UI can act on", () => {
  assert.deepEqual(
    listInstallableAgents().map((agent) => agent.agent),
    ["codex", "claude", "github-copilot", "gemini"]
  );
});
