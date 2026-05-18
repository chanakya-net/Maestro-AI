import assert from "node:assert/strict";
import test from "node:test";
import { buildRunnerCreateOptions } from "./runnerContainers";
import type { Project } from "../types";

test("runner mounts agent home as a stable named volume", () => {
  const project: Project = {
    id: "project-123",
    name: "Demo",
    hostPath: "/tmp/demo-project",
    workspacePath: "/workspace",
    runnerContainerName: "ai-skills-runner-project-123",
    homeVolumeName: "ai-skills-agent-home-project-123",
    createdAt: "2026-05-18T00:00:00.000Z"
  };

  const options = buildRunnerCreateOptions(project);

  assert.equal(options.name, project.runnerContainerName);
  assert.equal(options.WorkingDir, "/workspace");
  assert.ok(options.Env?.includes("HOME=/home/agent"));
  assert.deepEqual(options.HostConfig?.Binds, [
    "/tmp/demo-project:/workspace",
    "ai-skills-agent-home-project-123:/home/agent"
  ]);
});
