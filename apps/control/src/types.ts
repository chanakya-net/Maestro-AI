export type AgentId = "codex" | "claude" | "github-copilot" | "gemini";

export type Project = {
  id: string;
  name: string;
  hostPath: string;
  workspacePath: "/workspace";
  runnerContainerName: string;
  homeVolumeName: string;
  createdAt: string;
};

export type RunnerStatus = {
  exists: boolean;
  running: boolean;
  containerId?: string;
  name?: string;
  image?: string;
};

export type AgentStatus = {
  agent: AgentId;
  displayName: string;
  installed: boolean;
  authenticated: boolean;
  installable: boolean;
  canLogin: boolean;
  experimental?: boolean;
  models: string[];
  defaultModel?: string;
  note?: string;
  loginNote?: string;
  authSettingsUrl?: string;
};

export type RunnerCommandResult = {
  exitCode: number;
  output: string;
};
