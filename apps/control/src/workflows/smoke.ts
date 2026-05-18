import type { AgentId } from "../types";

export type SmokeWorkflowRequest = {
  agent: AgentId;
  model: string;
};

export function buildSmokeCommand(request: SmokeWorkflowRequest): string[] {
  return [
    "bash",
    "-lc",
    [
      "printf '%s\\n' 'This is a Docker auth smoke test. Do not edit files. Reply with exactly TOOL_OK if you can run.' > /tmp/context.md",
      "printf '%s\\n' 'Return only the requested marker. Do not include explanation.' > /tmp/prompt.md",
      `/opt/ai-skills/assets/run-agent.sh --agent ${shellQuote(request.agent)} --model ${shellQuote(request.model)} --context-file /tmp/context.md --prompt-file /tmp/prompt.md --permission-mode safe --unattended`
    ].join(" && ")
  ];
}

function shellQuote(value: string): string {
  return `'${value.replace(/'/g, "'\"'\"'")}'`;
}
