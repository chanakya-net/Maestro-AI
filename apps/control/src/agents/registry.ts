import fs from "node:fs/promises";
import path from "node:path";
import type { AgentId, AgentStatus } from "../types";
import { getAgentDefinition } from "./definitions";

type RegistryAgent = {
  display_name: string;
  model?: {
    default?: string;
    known_models?: string[];
  };
};

type AgentRegistry = {
  agents: Record<string, RegistryAgent>;
};

const supportedAgents: AgentId[] = ["codex", "claude", "github-copilot", "gemini"];

export async function listRegistryBackedAgentStatuses(): Promise<AgentStatus[]> {
  const registry = await readRegistry();
  return supportedAgents.map((agent) => {
    const entry = registry.agents[agent];
    return {
      agent,
      displayName: entry?.display_name ?? agent,
      installed: false,
      authenticated: false,
      installable: Boolean(getAgentDefinition(agent).installCommand),
      canLogin: Boolean(getAgentDefinition(agent).loginCommand),
      experimental: getAgentDefinition(agent).experimental,
      models: entry?.model?.known_models ?? [],
      defaultModel: entry?.model?.default,
      note: "Install/auth checks run inside the project runner in the next implementation slice."
    };
  });
}

export async function listModels(agent: AgentId): Promise<string[]> {
  const registry = await readRegistry();
  return registry.agents[agent]?.model?.known_models ?? [];
}

async function readRegistry(): Promise<AgentRegistry> {
  const repoRoot = process.env.AI_SKILLS_REPO_ROOT ?? path.resolve(process.cwd(), "../..");
  const registryPath = path.join(repoRoot, "assets", "agent-registry.json");
  const raw = await fs.readFile(registryPath, "utf8");
  return JSON.parse(raw) as AgentRegistry;
}
