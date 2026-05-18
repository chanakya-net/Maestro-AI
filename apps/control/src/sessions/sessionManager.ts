import { EventEmitter } from "node:events";
import type { Readable } from "node:stream";
import type Docker from "dockerode";
import type { Project } from "../types";

export type ManagedSessionStatus = "starting" | "running" | "completed" | "failed";

export type ManagedSessionEvent =
  | { type: "session"; id: string; title: string; status: ManagedSessionStatus }
  | { type: "stdout"; text: string }
  | { type: "stderr"; text: string }
  | { type: "url"; url: string }
  | { type: "code"; code: string }
  | { type: "exit"; code: number };

export type ManagedSession = {
  id: string;
  title: string;
  projectId: string;
  command: string[];
  status: ManagedSessionStatus;
  createdAt: string;
};

type ActiveSession = ManagedSession & {
  events: ManagedSessionEvent[];
  emitter: EventEmitter;
  stream?: NodeJS.WritableStream;
};

export class DockerSessionManager {
  private readonly sessions = new Map<string, ActiveSession>();

  constructor(private readonly docker: Docker) {}

  async start(project: Project, title: string, command: string[]): Promise<ManagedSession> {
    const session = this.createSession(project, title, command);
    this.append(session, { type: "session", id: session.id, title, status: "starting" });

    void this.run(project, session);
    return this.publicSession(session);
  }

  get(id: string): ManagedSession | undefined {
    const session = this.sessions.get(id);
    return session ? this.publicSession(session) : undefined;
  }

  subscribe(id: string, onEvent: (event: ManagedSessionEvent) => void): () => void {
    const session = this.requireSession(id);
    session.events.forEach(onEvent);
    session.emitter.on("event", onEvent);
    return () => session.emitter.off("event", onEvent);
  }

  write(id: string, input: string): void {
    const session = this.requireSession(id);
    if (!session.stream) {
      throw new Error("Session is not ready for input.");
    }
    session.stream.write(input.endsWith("\n") ? input : `${input}\n`);
  }

  private async run(project: Project, session: ActiveSession): Promise<void> {
    try {
      const container = this.docker.getContainer(project.runnerContainerName);
      const exec = await container.exec({
        Cmd: session.command,
        AttachStdin: true,
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

      const stream = (await exec.start({ hijack: true, stdin: true })) as unknown as NodeJS.ReadWriteStream;
      session.stream = stream;
      session.status = "running";
      this.append(session, { type: "session", id: session.id, title: session.title, status: "running" });

      stream.on("data", (chunk: Buffer) => {
        const text = redactSecrets(sanitizeTerminalText(chunk.toString("utf8")));
        this.append(session, { type: "stdout", text });
        for (const url of extractUrls(text)) {
          this.append(session, { type: "url", url });
        }
        for (const code of extractDeviceCodes(text)) {
          this.append(session, { type: "code", code });
        }
      });

      stream.on("error", (error: Error) => {
        session.status = "failed";
        this.append(session, { type: "stderr", text: error.message });
      });

      stream.on("end", async () => this.finish(session, exec));
      stream.on("close", async () => this.finish(session, exec));
    } catch (error) {
      session.status = "failed";
      this.append(session, { type: "stderr", text: (error as Error).message });
      this.append(session, { type: "exit", code: 1 });
    }
  }

  private async finish(session: ActiveSession, exec: Docker.Exec): Promise<void> {
    if (session.status === "completed" || session.status === "failed") {
      return;
    }

    const info = await exec.inspect();
    const code = info.ExitCode ?? 0;
    session.status = code === 0 ? "completed" : "failed";
    this.append(session, { type: "exit", code });
    this.append(session, { type: "session", id: session.id, title: session.title, status: session.status });
  }

  private createSession(project: Project, title: string, command: string[]): ActiveSession {
    const id = crypto.randomUUID();
    const session: ActiveSession = {
      id,
      title,
      projectId: project.id,
      command,
      status: "starting",
      createdAt: new Date().toISOString(),
      events: [],
      emitter: new EventEmitter()
    };
    this.sessions.set(id, session);
    return session;
  }

  private append(session: ActiveSession, event: ManagedSessionEvent): void {
    session.events.push(event);
    session.emitter.emit("event", event);
  }

  private requireSession(id: string): ActiveSession {
    const session = this.sessions.get(id);
    if (!session) {
      throw new Error("Session not found.");
    }
    return session;
  }

  private publicSession(session: ActiveSession): ManagedSession {
    return {
      id: session.id,
      title: session.title,
      projectId: session.projectId,
      command: session.command,
      status: session.status,
      createdAt: session.createdAt
    };
  }
}

export function toServerSentEvent(event: ManagedSessionEvent): string {
  return `event: ${event.type}\ndata: ${JSON.stringify(event)}\n\n`;
}

export function redactSecrets(text: string): string {
  return text
    .replace(/Bearer\s+[A-Za-z0-9._~+/=-]+/g, "Bearer [REDACTED]")
    .replace(/\b(sk-[A-Za-z0-9_-]{16,}|sk-proj-[A-Za-z0-9_-]+)/g, "[REDACTED]")
    .replace(/\b([A-Z0-9_]*(?:TOKEN|SECRET|API_KEY|ACCESS_KEY)[A-Z0-9_]*)=([^\s]+)/gi, "$1=[REDACTED]");
}

export function sanitizeTerminalText(text: string): string {
  return text
    .replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, "")
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\ufffd]/g, "");
}

export function extractUrls(text: string): string[] {
  return [...text.matchAll(/https?:\/\/[^\s)]+/g)].map((match) => match[0].replace(/[.,;:!?]+$/, ""));
}

export function extractDeviceCodes(text: string): string[] {
  return [...text.matchAll(/\b[A-Z0-9]{4,8}-[A-Z0-9]{4,8}\b/g)].map((match) => match[0]);
}
