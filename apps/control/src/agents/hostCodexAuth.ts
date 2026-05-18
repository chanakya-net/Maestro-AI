import { execFile as defaultExecFile } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import type { Project } from "../types";

const execFileAsync = promisify(defaultExecFile);
const codexFiles = ["auth.json", "config.toml", "version.json"] as const;

export type HostCodexAuthImportResult = {
  importedFiles: string[];
};

type HostCodexAuthDependencies = {
  homeDir?: string;
  pathExists?: (filePath: string) => Promise<boolean>;
  execFile?: (file: string, args: string[]) => Promise<void>;
};

export async function importHostCodexAuth(
  project: Project,
  dependencies: HostCodexAuthDependencies = {}
): Promise<HostCodexAuthImportResult> {
  const homeDir = dependencies.homeDir ?? os.homedir();
  const pathExists = dependencies.pathExists ?? fileExists;
  const execFile = dependencies.execFile ?? dockerExecFile;
  const hostCodexDir = path.join(homeDir, ".codex");

  for (const file of codexFiles) {
    const filePath = path.join(hostCodexDir, file);
    if (!(await pathExists(filePath))) {
      throw new Error(`Missing host Codex auth file: ${file}`);
    }
  }

  await execFile("docker", [
    "exec",
    "-u",
    "root",
    project.runnerContainerName,
    "bash",
    "-lc",
    "mkdir -p /home/agent/.codex"
  ]);

  for (const file of codexFiles) {
    await execFile("docker", [
      "cp",
      path.join(hostCodexDir, file),
      `${project.runnerContainerName}:/home/agent/.codex/${file}`
    ]);
  }

  await execFile("docker", [
    "exec",
    "-u",
    "root",
    project.runnerContainerName,
    "bash",
    "-lc",
    "chown -R agent:agent /home/agent/.codex && chmod 700 /home/agent/.codex && chmod 600 /home/agent/.codex/auth.json /home/agent/.codex/config.toml /home/agent/.codex/version.json"
  ]);

  return { importedFiles: [...codexFiles] };
}

async function fileExists(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

async function dockerExecFile(file: string, args: string[]): Promise<void> {
  await execFileAsync(file, args);
}
