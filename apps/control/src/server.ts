import express, { type NextFunction, type Request, type Response } from "express";
import path from "node:path";
import { createDockerClient } from "./docker/dockerClient";
import { RunnerContainerManager } from "./docker/runnerContainers";
import { listModels } from "./agents/registry";
import { isAgentId } from "./agents/definitions";
import { listRunnerAgentStatuses, requireAgentAction } from "./agents/status";
import { importHostCodexAuth } from "./agents/hostCodexAuth";
import { DockerSessionManager, toServerSentEvent } from "./sessions/sessionManager";
import { ProjectStore } from "./store";
import type { AgentId } from "./types";
import { buildWorkflowCommand, getWorkflowTitle, isWorkflowId } from "./workflows";

const app = express();
const store = new ProjectStore();
const docker = createDockerClient();
const runnerManager = new RunnerContainerManager(docker);
const sessionManager = new DockerSessionManager(docker);
const host = process.env.HOST ?? "127.0.0.1";
const port = Number(process.env.PORT ?? 3000);

app.disable("x-powered-by");
app.use(express.json({ limit: "1mb" }));

app.get("/api/health", (_request, response) => {
  response.json({ ok: true });
});

app.get("/api/projects", asyncHandler(async (_request, response) => {
  response.json({ projects: await store.listProjects() });
}));

app.post("/api/projects", asyncHandler(async (request, response) => {
  const { name, hostPath } = request.body as { name?: string; hostPath?: string };
  if (!hostPath) {
    response.status(400).json({ error: "hostPath is required" });
    return;
  }

  const project = await store.createProject({ name: name ?? "", hostPath });
  response.status(201).json({ project });
}));

app.get("/api/projects/:id", asyncHandler(async (request, response) => {
  const project = await requireProject(request.params.id);
  response.json({ project });
}));

app.post("/api/projects/:id/runner/start", asyncHandler(async (request, response) => {
  const project = await requireProject(request.params.id);
  response.json({ status: await runnerManager.start(project) });
}));

app.post("/api/projects/:id/runner/stop", asyncHandler(async (request, response) => {
  const project = await requireProject(request.params.id);
  response.json({ status: await runnerManager.stop(project) });
}));

app.get("/api/projects/:id/runner/status", asyncHandler(async (request, response) => {
  const project = await requireProject(request.params.id);
  response.json({ status: await runnerManager.status(project) });
}));

app.get("/api/projects/:id/agents/status", asyncHandler(async (request, response) => {
  const project = await requireProject(request.params.id);
  response.json({ agents: await listRunnerAgentStatuses(project, runnerManager) });
}));

app.get("/api/projects/:id/agents/:agent/models", asyncHandler(async (request, response) => {
  const agent = request.params.agent as AgentId;
  if (!isAgentId(agent)) {
    response.status(404).json({ error: "unknown agent" });
    return;
  }

  response.json({ models: await listModels(agent) });
}));

app.post("/api/projects/:id/agents/:agent/install", asyncHandler(async (request, response) => {
  const project = await requireProject(request.params.id);
  await requireRunningRunner(project);
  const { definition, command } = requireAgentAction(request.params.agent, "installCommand");
  const session = await sessionManager.start(project, `Install ${definition.displayName}`, command);
  response.status(202).json({ session });
}));

app.post("/api/projects/:id/agents/:agent/login", asyncHandler(async (request, response) => {
  const project = await requireProject(request.params.id);
  await requireRunningRunner(project);
  const { definition, command } = requireAgentAction(request.params.agent, "loginCommand");
  const session = await sessionManager.start(project, `Login ${definition.displayName}`, command);
  response.status(202).json({ session });
}));

app.post("/api/projects/:id/agents/codex/import-host-auth", asyncHandler(async (request, response) => {
  const project = await requireProject(request.params.id);
  await requireRunningRunner(project);
  const result = await importHostCodexAuth(project);
  response.json(result);
}));

app.post("/api/projects/:id/workflows/:workflow", asyncHandler(async (request, response) => {
  const project = await requireProject(request.params.id);
  await requireRunningRunner(project);

  const workflow = request.params.workflow;
  const { agent, model } = request.body as { agent?: string; model?: string };
  if (!isWorkflowId(workflow)) {
    response.status(404).json({ error: "unknown workflow" });
    return;
  }
  if (!agent || !isAgentId(agent)) {
    response.status(400).json({ error: "valid agent is required" });
    return;
  }
  if (!model) {
    response.status(400).json({ error: "model is required" });
    return;
  }

  const command = buildWorkflowCommand(workflow, { agent, model });
  const session = await sessionManager.start(project, getWorkflowTitle(workflow, agent), command);
  response.status(202).json({ session });
}));

app.get("/api/sessions/:id/events", (request, response, next) => {
  try {
    if (!sessionManager.get(request.params.id)) {
      response.status(404).json({ error: "Session not found." });
      return;
    }

    response.writeHead(200, {
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "Content-Type": "text/event-stream",
      "X-Accel-Buffering": "no"
    });

    const unsubscribe = sessionManager.subscribe(request.params.id, (event) => {
      response.write(toServerSentEvent(event));
    });

    request.on("close", unsubscribe);
  } catch (error) {
    next(error);
  }
});

app.post("/api/sessions/:id/input", asyncHandler(async (request, response) => {
  const { input } = request.body as { input?: string };
  if (!input) {
    response.status(400).json({ error: "input is required" });
    return;
  }
  sessionManager.write(request.params.id, input);
  response.json({ ok: true });
}));

const webDist = path.resolve(__dirname, "..", "web");
app.use(express.static(webDist));
app.get("*", (_request, response) => {
  response.sendFile(path.join(webDist, "index.html"));
});

app.use((error: Error, _request: Request, response: Response, _next: NextFunction) => {
  if (response.headersSent) {
    return;
  }
  const status = error.message === "Project not found." ? 404 : 500;
  response.status(status).json({ error: error.message });
});

app.listen(port, host, () => {
  console.log(`AI-Skills control app listening on http://${host}:${port}`);
});

async function requireProject(id: string) {
  const project = await store.getProject(id);
  if (!project) {
    throw new Error("Project not found.");
  }
  return project;
}

async function requireRunningRunner(project: Awaited<ReturnType<typeof requireProject>>) {
  const status = await runnerManager.status(project);
  if (!status.running) {
    throw new Error("Start the project runner before running agent commands.");
  }
}

function asyncHandler(
  handler: (request: Request, response: Response, next: NextFunction) => Promise<void>
) {
  return (request: Request, response: Response, next: NextFunction) => {
    handler(request, response, next).catch(next);
  };
}
