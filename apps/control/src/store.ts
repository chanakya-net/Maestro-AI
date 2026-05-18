import fs from "node:fs/promises";
import path from "node:path";
import { randomUUID } from "node:crypto";
import type { Project } from "./types";

type StoreShape = {
  projects: Project[];
};

const defaultStorePath = path.resolve(process.cwd(), "var", "projects.json");

export class ProjectStore {
  private readonly filePath: string;

  constructor(filePath = process.env.AI_SKILLS_CONTROL_DATA ?? defaultStorePath) {
    this.filePath = filePath;
  }

  async listProjects(): Promise<Project[]> {
    const store = await this.read();
    return store.projects;
  }

  async getProject(id: string): Promise<Project | undefined> {
    const store = await this.read();
    return store.projects.find((project) => project.id === id);
  }

  async createProject(input: { name: string; hostPath: string }): Promise<Project> {
    const realHostPath = await fs.realpath(input.hostPath);
    const stat = await fs.stat(realHostPath);
    if (!stat.isDirectory()) {
      throw new Error("Project path must be a directory.");
    }

    const id = randomUUID().slice(0, 12);
    const project: Project = {
      id,
      name: input.name.trim() || path.basename(realHostPath),
      hostPath: realHostPath,
      workspacePath: "/workspace",
      runnerContainerName: `ai-skills-runner-${id}`,
      homeVolumeName: `ai-skills-agent-home-${id}`,
      createdAt: new Date().toISOString()
    };

    const store = await this.read();
    store.projects.push(project);
    await this.write(store);
    return project;
  }

  private async read(): Promise<StoreShape> {
    try {
      const raw = await fs.readFile(this.filePath, "utf8");
      return JSON.parse(raw) as StoreShape;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        return { projects: [] };
      }
      throw error;
    }
  }

  private async write(store: StoreShape): Promise<void> {
    await fs.mkdir(path.dirname(this.filePath), { recursive: true });
    await fs.writeFile(this.filePath, `${JSON.stringify(store, null, 2)}\n`, "utf8");
  }
}
