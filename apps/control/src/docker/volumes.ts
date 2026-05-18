import type Docker from "dockerode";

export async function ensureVolume(docker: Docker, name: string): Promise<void> {
  try {
    await docker.getVolume(name).inspect();
  } catch (error) {
    if ((error as { statusCode?: number }).statusCode !== 404) {
      throw error;
    }
    await docker.createVolume({ Name: name });
  }
}
