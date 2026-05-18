# AI-Skills Docker Runtime

This folder contains the first Docker-backed implementation slice for the web GUI runner plan.

## Runner Image

Build:

```bash
docker build -t ai-skills-runner:latest -f docker/runner.Dockerfile .
```

Verify base tools:

```bash
docker run --rm ai-skills-runner:latest bash -lc 'node --version && git --version && jq --version'
```

The runner image intentionally does not install Codex, Claude, or Copilot by default. The control app installs selected agents into each project runner so auth and config can live in that project's `/home/agent` Docker volume.

## Control App Dev Compose

```bash
docker compose -f docker/docker-compose.dev.yml up --build
```

The compose setup mounts the Docker socket for local prototyping. Treat that as host-level access; this should be replaced by a native host helper before broader distribution.
