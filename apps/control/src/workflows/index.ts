import { getAgentDefinition } from "../agents/definitions";
import type { AgentId } from "../types";
import { buildSmokeCommand } from "./smoke";

export type WorkflowId = "smoke";

export type WorkflowRequest = {
  agent: AgentId;
  model: string;
};

export function isWorkflowId(value: string): value is WorkflowId {
  return value === "smoke";
}

export function buildWorkflowCommand(workflow: WorkflowId, request: WorkflowRequest): string[] {
  switch (workflow) {
    case "smoke":
      return buildSmokeCommand(request);
  }
}

export function getWorkflowTitle(workflow: WorkflowId, agent: AgentId): string {
  const definition = getAgentDefinition(agent);
  switch (workflow) {
    case "smoke":
      return `Smoke test ${definition.displayName}`;
  }
}
