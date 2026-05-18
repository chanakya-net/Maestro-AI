import assert from "node:assert/strict";
import test from "node:test";
import path from "node:path";
import type { Project } from "../types";
import { importHostCodexAuth } from "./hostCodexAuth";

test("importHostCodexAuth copies host Codex auth files into the runner home", async () => {
  const calls: Array<{ file: string; args: string[] }> = [];
  const project: Project = {
    id: "abc123",
    name: "Demo",
    hostPath: "/tmp/demo",
    workspacePath: "/workspace",
    runnerContainerName: "ai-skills-runner-abc123",
    homeVolumeName: "ai-skills-agent-home-abc123",
    createdAt: "2026-05-18T00:00:00.000Z"
  };

  await importHostCodexAuth(project, {
    homeDir: "/Users/tester",
    pathExists: async () => true,
    execFile: async (file, args) => {
      calls.push({ file, args });
    }
  });

  assert.deepEqual(calls, [
    {
      file: "docker",
      args: ["exec", "-u", "root", "ai-skills-runner-abc123", "bash", "-lc", "mkdir -p /home/agent/.codex"]
    },
    {
      file: "docker",
      args: [
        "cp",
        path.join("/Users/tester", ".codex/auth.json"),
        "ai-skills-runner-abc123:/home/agent/.codex/auth.json"
      ]
    },
    {
      file: "docker",
      args: [
        "cp",
        path.join("/Users/tester", ".codex/config.toml"),
        "ai-skills-runner-abc123:/home/agent/.codex/config.toml"
      ]
    },
    {
      file: "docker",
      args: [
        "cp",
        path.join("/Users/tester", ".codex/version.json"),
        "ai-skills-runner-abc123:/home/agent/.codex/version.json"
      ]
    },
    {
      file: "docker",
      args: [
        "exec",
        "-u",
        "root",
        "ai-skills-runner-abc123",
        "bash",
        "-lc",
        "chown -R agent:agent /home/agent/.codex && chmod 700 /home/agent/.codex && chmod 600 /home/agent/.codex/auth.json /home/agent/.codex/config.toml /home/agent/.codex/version.json"
      ]
    }
  ]);
});

test("importHostCodexAuth fails before copying when a required host file is missing", async () => {
  const calls: Array<{ file: string; args: string[] }> = [];

  await assert.rejects(
    () =>
      importHostCodexAuth(
        {
          id: "abc123",
          name: "Demo",
          hostPath: "/tmp/demo",
          workspacePath: "/workspace",
          runnerContainerName: "ai-skills-runner-abc123",
          homeVolumeName: "ai-skills-agent-home-abc123",
          createdAt: "2026-05-18T00:00:00.000Z"
        },
        {
          homeDir: "/Users/tester",
          pathExists: async (filePath) => !filePath.endsWith("auth.json"),
          execFile: async (file, args) => {
            calls.push({ file, args });
          }
        }
      ),
    /Missing host Codex auth file: auth\.json/
  );

  assert.deepEqual(calls, []);
});
