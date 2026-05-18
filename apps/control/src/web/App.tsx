import { FormEvent, useEffect, useMemo, useState } from "react";

type Project = {
  id: string;
  name: string;
  hostPath: string;
  runnerContainerName: string;
  homeVolumeName: string;
  createdAt: string;
};

type RunnerStatus = {
  exists: boolean;
  running: boolean;
  containerId?: string;
  name?: string;
  image?: string;
};

type AgentStatus = {
  agent: string;
  displayName: string;
  installed: boolean;
  authenticated: boolean;
  installable: boolean;
  canLogin: boolean;
  experimental?: boolean;
  models: string[];
  defaultModel?: string;
  note?: string;
  loginNote?: string;
  authSettingsUrl?: string;
};

type Session = {
  id: string;
  title: string;
  status: string;
};

type SessionEvent =
  | { type: "session"; id: string; title: string; status: string }
  | { type: "stdout"; text: string }
  | { type: "stderr"; text: string }
  | { type: "url"; url: string }
  | { type: "code"; code: string }
  | { type: "exit"; code: number };

export function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [runnerStatus, setRunnerStatus] = useState<RunnerStatus | null>(null);
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [name, setName] = useState("");
  const [hostPath, setHostPath] = useState("");
  const [message, setMessage] = useState("");
  const [activeSession, setActiveSession] = useState<Session | null>(null);
  const [sessionEvents, setSessionEvents] = useState<SessionEvent[]>([]);
  const [sessionInput, setSessionInput] = useState("");
  const [workflowAgent, setWorkflowAgent] = useState("");
  const [workflowModel, setWorkflowModel] = useState("");

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId),
    [projects, selectedProjectId]
  );
  const workflowAgentStatus = useMemo(
    () => agents.find((agent) => agent.agent === workflowAgent),
    [agents, workflowAgent]
  );

  useEffect(() => {
    void refreshProjects();
  }, []);

  useEffect(() => {
    if (!selectedProjectId) return;
    void refreshRunnerStatus(selectedProjectId);
    void refreshAgents(selectedProjectId);
  }, [selectedProjectId]);

  useEffect(() => {
    if (agents.length === 0) return;
    if (!agents.some((agent) => agent.agent === workflowAgent)) {
      setWorkflowAgent(agents[0].agent);
    }
  }, [agents, workflowAgent]);

  useEffect(() => {
    if (!workflowAgentStatus) return;
    if (!workflowAgentStatus.models.includes(workflowModel)) {
      setWorkflowModel(workflowAgentStatus.defaultModel || workflowAgentStatus.models[0] || "");
    }
  }, [workflowAgentStatus, workflowModel]);

  useEffect(() => {
    if (!activeSession) return;

    const eventSource = new EventSource(`/api/sessions/${activeSession.id}/events`);
    const eventTypes = ["session", "stdout", "stderr", "url", "code", "exit"];
    for (const type of eventTypes) {
      eventSource.addEventListener(type, (event) => {
        const parsed = JSON.parse((event as MessageEvent).data) as SessionEvent;
        setSessionEvents((current) => [...current, parsed]);
        if (parsed.type === "session") {
          setActiveSession((current) => (current ? { ...current, status: parsed.status } : current));
        }
        if (parsed.type === "exit" && selectedProjectId) {
          void refreshAgents(selectedProjectId);
        }
      });
    }

    return () => eventSource.close();
  }, [activeSession?.id, selectedProjectId]);

  async function refreshProjects() {
    const data = await api<{ projects: Project[] }>("/api/projects");
    setProjects(data.projects);
    setSelectedProjectId((current) => current || data.projects[0]?.id || "");
  }

  async function refreshRunnerStatus(projectId: string) {
    const data = await api<{ status: RunnerStatus }>(`/api/projects/${projectId}/runner/status`);
    setRunnerStatus(data.status);
  }

  async function refreshAgents(projectId: string) {
    const data = await api<{ agents: AgentStatus[] }>(`/api/projects/${projectId}/agents/status`);
    setAgents(data.agents);
  }

  async function createProject(event: FormEvent) {
    event.preventDefault();
    setMessage("");
    const data = await api<{ project: Project }>("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name, hostPath })
    });
    setProjects((current) => [...current, data.project]);
    setSelectedProjectId(data.project.id);
    setName("");
    setHostPath("");
    setMessage("Project created.");
  }

  async function startRunner() {
    if (!selectedProject) return;
    setMessage("Starting runner...");
    const data = await api<{ status: RunnerStatus }>(`/api/projects/${selectedProject.id}/runner/start`, {
      method: "POST"
    });
    setRunnerStatus(data.status);
    setMessage(data.status.running ? "Runner is running." : "Runner exists but is not running.");
  }

  async function stopRunner() {
    if (!selectedProject) return;
    setMessage("Stopping runner...");
    const data = await api<{ status: RunnerStatus }>(`/api/projects/${selectedProject.id}/runner/stop`, {
      method: "POST"
    });
    setRunnerStatus(data.status);
    setMessage("Runner stopped.");
  }

  async function startAgentAction(agent: string, action: "install" | "login") {
    if (!selectedProject) return;
    setMessage(`${action === "install" ? "Installing" : "Starting login for"} ${agent}...`);
    const data = await api<{ session: Session }>(
      `/api/projects/${selectedProject.id}/agents/${agent}/${action}`,
      { method: "POST" }
    );
    setActiveSession(data.session);
    setSessionEvents([]);
    setMessage(`${data.session.title} started.`);
  }

  async function runSmokeWorkflow() {
    if (!selectedProject || !workflowAgent || !workflowModel) return;
    setMessage("Starting smoke test...");
    const data = await api<{ session: Session }>(
      `/api/projects/${selectedProject.id}/workflows/smoke`,
      {
        method: "POST",
        body: JSON.stringify({ agent: workflowAgent, model: workflowModel })
      }
    );
    setActiveSession(data.session);
    setSessionEvents([]);
    setMessage(`${data.session.title} started.`);
  }

  async function importHostCodexAuth() {
    if (!selectedProject) return;
    setMessage("Importing host Codex auth...");
    await api(`/api/projects/${selectedProject.id}/agents/codex/import-host-auth`, {
      method: "POST"
    });
    await refreshAgents(selectedProject.id);
    setMessage("Host Codex auth imported into the runner.");
  }

  async function sendSessionInput(event: FormEvent) {
    event.preventDefault();
    if (!activeSession || !sessionInput.trim()) return;
    await api(`/api/sessions/${activeSession.id}/input`, {
      method: "POST",
      body: JSON.stringify({ input: sessionInput })
    });
    setSessionInput("");
  }

  return (
    <main>
      <header className="topbar">
        <div>
          <h1>AI-Skills Runner</h1>
          <p>Local Docker control plane for project-scoped coding agents.</p>
        </div>
        <span className="badge">localhost</span>
      </header>

      <section className="layout">
        <form className="panel" onSubmit={createProject}>
          <h2>Project</h2>
          <label>
            Name
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="my-app" />
          </label>
          <label>
            Code folder path
            <input
              value={hostPath}
              onChange={(event) => setHostPath(event.target.value)}
              placeholder="/Users/you/projects/my-app"
              required
            />
          </label>
          <button type="submit">Create project</button>
        </form>

        <section className="panel">
          <h2>Runner</h2>
          <label>
            Active project
            <select value={selectedProjectId} onChange={(event) => setSelectedProjectId(event.target.value)}>
              <option value="">Select a project</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </label>

          {selectedProject && (
            <dl className="facts">
              <dt>Folder</dt>
              <dd>{selectedProject.hostPath}</dd>
              <dt>Container</dt>
              <dd>{selectedProject.runnerContainerName}</dd>
              <dt>Auth volume</dt>
              <dd>{selectedProject.homeVolumeName}</dd>
            </dl>
          )}

          <div className="actions">
            <button type="button" onClick={startRunner} disabled={!selectedProject}>
              Start
            </button>
            <button type="button" onClick={stopRunner} disabled={!selectedProject || !runnerStatus?.exists}>
              Stop
            </button>
          </div>

          <p className={runnerStatus?.running ? "status good" : "status"}>
            {runnerStatus?.running ? "Running" : runnerStatus?.exists ? "Stopped" : "No runner yet"}
          </p>
        </section>
      </section>

      <section className="panel wide">
        <h2>Agents and Models</h2>
        <div className="agent-grid">
          {agents.map((agent) => (
            <article className="agent" key={agent.agent}>
              <div>
                <h3>{agent.displayName}</h3>
                <p>{agent.agent}</p>
              </div>
              <div className="chips">
                <span>{agent.installed ? "Installed" : "Not installed"}</span>
                <span>{agent.authenticated ? "Authenticated" : "Needs auth"}</span>
                {agent.experimental && <span>Experimental</span>}
              </div>
              <p className="default-model">Default: {agent.defaultModel || "none"}</p>
              <select defaultValue={agent.defaultModel || agent.models[0] || ""}>
                {agent.models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
              {agent.note && <p className="note">{agent.note}</p>}
              {agent.loginNote && !agent.authenticated && (
                <p className="note">
                  {agent.loginNote}{" "}
                  {agent.authSettingsUrl && (
                    <a href={agent.authSettingsUrl} target="_blank" rel="noreferrer">
                      Open settings
                    </a>
                  )}
                </p>
              )}
              <div className="actions compact">
                <button
                  type="button"
                  disabled={!selectedProject || !runnerStatus?.running || !agent.installable}
                  onClick={() => startAgentAction(agent.agent, "install")}
                >
                  Install
                </button>
                <button
                  type="button"
                  disabled={
                    !selectedProject ||
                    !runnerStatus?.running ||
                    !agent.canLogin ||
                    !agent.installed ||
                    agent.authenticated
                  }
                  onClick={() => startAgentAction(agent.agent, "login")}
                >
                  Login
                </button>
                {agent.agent === "codex" && (
                  <button
                    type="button"
                    disabled={!selectedProject || !runnerStatus?.running || agent.authenticated}
                    onClick={importHostCodexAuth}
                  >
                    Import host auth
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="panel wide">
        <h2>Workflow Dashboard</h2>
        <div className="workflow-row">
          <label>
            Agent
            <select value={workflowAgent} onChange={(event) => setWorkflowAgent(event.target.value)}>
              {agents.map((agent) => (
                <option key={agent.agent} value={agent.agent}>
                  {agent.displayName}
                </option>
              ))}
            </select>
          </label>
          <label>
            Model
            <select value={workflowModel} onChange={(event) => setWorkflowModel(event.target.value)}>
              {(workflowAgentStatus?.models ?? []).map((model) => (
                <option key={model} value={model}>
                  {model}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            onClick={runSmokeWorkflow}
            disabled={!selectedProject || !runnerStatus?.running || !workflowAgent || !workflowModel}
          >
            Run smoke test
          </button>
        </div>
      </section>

      <section className="panel wide">
        <h2>Live Session</h2>
        {activeSession ? (
          <>
            <div className="session-header">
              <strong>{activeSession.title}</strong>
              <span className={activeSession.status === "completed" ? "status good" : "status"}>
                {activeSession.status}
              </span>
            </div>
            <div className="transcript" aria-live="polite">
              {sessionEvents.length === 0 && <p>Waiting for output...</p>}
              {sessionEvents.map((event, index) => (
                <SessionEventLine event={event} key={`${event.type}-${index}`} />
              ))}
            </div>
            <form className="session-input" onSubmit={sendSessionInput}>
              <input
                value={sessionInput}
                onChange={(event) => setSessionInput(event.target.value)}
                placeholder="Paste auth code or CLI input"
              />
              <button type="submit">Send</button>
            </form>
          </>
        ) : (
          <p className="empty">Start an install, login, or workflow run to see live output here.</p>
        )}
      </section>

      {message && <p className="toast">{message}</p>}
    </main>
  );
}

function SessionEventLine({ event }: { event: SessionEvent }) {
  if (event.type === "stdout" || event.type === "stderr") {
    return <pre className={event.type}>{event.text}</pre>;
  }
  if (event.type === "url") {
    const label = event.url.includes("auth.openai.com") ? "Auth URL" : "Link";
    return (
      <p className="url-line">
        {label}:{" "}
        <a href={event.url} target="_blank" rel="noreferrer">
          {event.url}
        </a>
      </p>
    );
  }
  if (event.type === "code") {
    return (
      <p className="code-line">
        Device code: <strong>{event.code}</strong>
      </p>
    );
  }
  if (event.type === "exit") {
    return <p className={event.code === 0 ? "event good" : "event bad"}>Process exited with {event.code}</p>;
  }
  return <p className="event">{event.title}: {event.status}</p>;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Request failed");
  }
  return data as T;
}
