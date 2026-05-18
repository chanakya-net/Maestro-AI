import Docker from "dockerode";

export function createDockerClient(): Docker {
  return new Docker({ socketPath: process.env.DOCKER_SOCKET ?? "/var/run/docker.sock" });
}
