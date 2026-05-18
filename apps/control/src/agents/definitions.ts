import type { AgentId } from "../types";

export type AgentDefinition = {
  agent: AgentId;
  displayName: string;
  installCommand?: string[];
  loginCommand?: string[];
  statusCommand?: string[];
  loginNote?: string;
  authSettingsUrl?: string;
  experimental?: boolean;
};

const definitions: Record<AgentId, AgentDefinition> = {
  codex: {
    agent: "codex",
    displayName: "Codex",
    installCommand: [
      "bash",
      "-lc",
      "export NPM_CONFIG_PREFIX=\"$HOME/.local\" NPM_CONFIG_CACHE=\"$HOME/.npm\" PATH=\"$HOME/.local/bin:$PATH\"; npm install -g @openai/codex && /opt/ai-skills/install.sh --only codex --no-color"
    ],
    loginCommand: ["codex", "login", "--device-auth"],
    statusCommand: ["codex", "login", "status"],
    loginNote:
      "Codex device auth requires device code authorization to be enabled in ChatGPT Security Settings. If Continue is disabled, enable that setting and start login again.",
    authSettingsUrl: "https://chatgpt.com/#settings/Security"
  },
  claude: {
    agent: "claude",
    displayName: "Claude",
    installCommand: [
      "bash",
      "-lc",
      "export NPM_CONFIG_PREFIX=\"$HOME/.local\" NPM_CONFIG_CACHE=\"$HOME/.npm\" PATH=\"$HOME/.local/bin:$PATH\"; npm install -g @anthropic-ai/claude-code && /opt/ai-skills/install.sh --only claude --no-color"
    ],
    loginCommand: ["claude", "auth", "login", "--claudeai"],
    statusCommand: ["claude", "auth", "status", "--text"]
  },
  "github-copilot": {
    agent: "github-copilot",
    displayName: "GitHub Copilot CLI",
    installCommand: [
      "bash",
      "-lc",
      "export NPM_CONFIG_PREFIX=\"$HOME/.local\" NPM_CONFIG_CACHE=\"$HOME/.npm\" PATH=\"$HOME/.local/bin:$PATH\"; npm install -g @github/copilot && /opt/ai-skills/install.sh --only copilot --no-color"
    ],
    loginCommand: ["copilot", "login"],
    statusCommand: [
      "bash",
      "-lc",
      "test -n \"$COPILOT_GITHUB_TOKEN\" || test -n \"$GH_TOKEN\" || test -n \"$GITHUB_TOKEN\" || test -s \"$HOME/.copilot/config.json\" || test -s \"$HOME/.copilot/settings.json\" || gh auth token >/dev/null 2>&1"
    ]
  },
  gemini: {
    agent: "gemini",
    displayName: "Gemini CLI",
    installCommand: [
      "bash",
      "-lc",
      "export NPM_CONFIG_PREFIX=\"$HOME/.local\" NPM_CONFIG_CACHE=\"$HOME/.npm\" PATH=\"$HOME/.local/bin:$PATH\"; npm install -g @google/gemini-cli && /opt/ai-skills/install.sh --only gemini --no-color"
    ],
    loginCommand: ["gemini"],
    statusCommand: [
      "bash",
      "-lc",
      "test -n \"$GEMINI_API_KEY\" || test -n \"$GOOGLE_API_KEY\" || test -n \"$GOOGLE_APPLICATION_CREDENTIALS\" || find \"$HOME/.gemini\" -type f \\( -name '*oauth*' -o -name '*cred*' -o -name '*token*' \\) 2>/dev/null | grep -q ."
    ],
    loginNote:
      "Gemini CLI in Docker works best with headless credentials such as GEMINI_API_KEY, GOOGLE_API_KEY, or Vertex AI environment variables. Interactive Google login may require a browser callback that can reach the runner."
  }
};

export function getAgentDefinition(agent: AgentId): AgentDefinition {
  return definitions[agent];
}

export function listInstallableAgents(): AgentDefinition[] {
  return Object.values(definitions);
}

export function isAgentId(value: string): value is AgentId {
  return value === "codex" || value === "claude" || value === "github-copilot" || value === "gemini";
}
