import type Docker from "dockerode";
import type { Project, RunnerCommandResult, RunnerStatus } from "../types";
import { ensureVolume } from "./volumes";

const runnerImage = process.env.AI_SKILLS_RUNNER_IMAGE ?? "ai-skills-runner:latest";

export class RunnerContainerManager {
  constructor(private readonly docker: Docker) {}

  async start(project: Project): Promise<RunnerStatus> {
    await ensureVolume(this.docker, project.homeVolumeName);

    const existing = await this.find(project.runnerContainerName);
    if (existing) {
      const info = await existing.inspect();
      if (!info.State.Running) {
        await existing.start();
      }
      return this.status(project);
    }

    const container = await this.docker.createContainer(buildRunnerCreateOptions(project));

    await container.start();
    return this.status(project);
  }

  async stop(project: Project): Promise<RunnerStatus> {
    const container = await this.find(project.runnerContainerName);
    if (!container) {
      return { exists: false, running: false };
    }

    const info = await container.inspect();
    if (info.State.Running) {
      await container.stop({ t: 5 });
    }
    return this.status(project);
  }

  async status(project: Project): Promise<RunnerStatus> {
    const container = await this.find(project.runnerContainerName);
    if (!container) {
      return { exists: false, running: false };
    }

    const info = await container.inspect();
    return {
      exists: true,
      running: Boolean(info.State.Running),
      containerId: info.Id,
      name: info.Name.replace(/^\//, ""),
      image: info.Config.Image
    };
  }

  async exec(project: Project, command: string[]): Promise<RunnerCommandResult> {
    const container = await this.find(project.runnerContainerName);
    if (!container) {
      throw new Error("Runner container does not exist.");
    }

    const info = await container.inspect();
    if (!info.State.Running) {
      throw new Error("Runner container is not running.");
    }

    const exec = await container.exec({
      Cmd: command,
      AttachStdout: true,
      AttachStderr: true,
      Tty: true,
      WorkingDir: "/workspace",
      Env: [
        "HOME=/home/agent",
        "NPM_CONFIG_PREFIX=/home/agent/.local",
        "NPM_CONFIG_CACHE=/home/agent/.npm",
        "PATH=/home/agent/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin",
        "AI_SKILLS_ROOT=/opt/ai-skills"
      ]
    });

    const stream = (await exec.start({})) as unknown as NodeJS.ReadableStream;
    let output = "";
    await new Promise<void>((resolve, reject) => {
      stream.on("data", (chunk: Buffer) => {
        output += chunk.toString("utf8");
      });
      stream.on("error", reject);
      stream.on("end", resolve);
      stream.on("close", resolve);
    });
    const inspected = await exec.inspect();
    return { exitCode: inspected.ExitCode ?? 0, output };
  }

  private async find(name: string) {
    try {
      const container = this.docker.getContainer(name);
      await container.inspect();
      return container;
    } catch (error) {
      if ((error as { statusCode?: number }).statusCode === 404) {
        return undefined;
      }
      throw error;
    }
  }
}

export function buildRunnerCreateOptions(project: Project): Docker.ContainerCreateOptions {
  return {
    Image: runnerImage,
    name: project.runnerContainerName,
    Env: runnerEnvironment(),
    WorkingDir: "/workspace",
    Cmd: ["bash", "-lc", "sleep infinity"],
    HostConfig: {
      Binds: [
        `${project.hostPath}:/workspace`,
        `${project.homeVolumeName}:/home/agent`
      ]
    }
  };
}

function runnerEnvironment(): string[] {
  return [
    "HOME=/home/agent",
    "NPM_CONFIG_PREFIX=/home/agent/.local",
    "NPM_CONFIG_CACHE=/home/agent/.npm",
    "PATH=/home/agent/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin",
    "AI_SKILLS_ROOT=/opt/ai-skills"
  ];
}
