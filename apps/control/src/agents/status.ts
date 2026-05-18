import type { RunnerContainerManager } from "../docker/runnerContainers";
import type { Project, AgentStatus } from "../types";
import { getAgentDefinition, isAgentId, listInstallableAgents } from "./definitions";
import { listModels } from "./registry";

const detectionCommands = {
  codex: ["bash", "-lc", "command -v codex >/dev/null 2>&1"],
  claude: ["bash", "-lc", "command -v claude >/dev/null 2>&1"],
  "github-copilot": ["bash", "-lc", "command -v copilot >/dev/null 2>&1"],
  gemini: ["bash", "-lc", "command -v gemini >/dev/null 2>&1"]
} as const;

export async function listRunnerAgentStatuses(
  project: Project,
  runnerManager: RunnerContainerManager
): Promise<AgentStatus[]> {
  const runnerStatus = await runnerManager.status(project);
  const definitions = listInstallableAgents();

  return Promise.all(
    definitions.map(async (definition) => {
      const models = await listModels(definition.agent);
      if (!runnerStatus.running) {
        return {
          agent: definition.agent,
          displayName: definition.displayName,
          installed: false,
          authenticated: false,
          installable: Boolean(definition.installCommand),
          canLogin: Boolean(definition.loginCommand),
          experimental: definition.experimental,
          models,
          loginNote: definition.loginNote,
          authSettingsUrl: definition.authSettingsUrl,
          note: "Start the runner before checking install/auth status."
        };
      }

      const installed = await exitsZero(project, runnerManager, detectionCommands[definition.agent]);
      const authenticated =
        installed && definition.statusCommand
          ? await exitsZero(project, runnerManager, definition.statusCommand)
          : false;

      return {
        agent: definition.agent,
        displayName: definition.displayName,
        installed,
        authenticated,
        installable: Boolean(definition.installCommand),
        canLogin: Boolean(definition.loginCommand),
        experimental: definition.experimental,
        models,
        defaultModel: models[0],
        loginNote: definition.loginNote,
        authSettingsUrl: definition.authSettingsUrl,
        note: definition.experimental ? "Experimental: install/auth flow is not verified yet." : undefined
      };
    })
  );
}

export function requireAgentAction(agent: string, action: "installCommand" | "loginCommand") {
  if (!isAgentId(agent)) {
    throw new Error("Unknown agent.");
  }
  const definition = getAgentDefinition(agent);
  const command = definition?.[action];
  if (!command) {
    throw new Error(`${definition?.displayName ?? agent} does not support this action yet.`);
  }
  return { definition, command };
}

async function exitsZero(
  project: Project,
  runnerManager: RunnerContainerManager,
  command: readonly string[]
): Promise<boolean> {
  try {
    const result = await runnerManager.exec(project, [...command]);
    return result.exitCode === 0;
  } catch {
    return false;
  }
}
