export type SessionStatus =
  | "starting"
  | "running"
  | "waiting_for_user"
  | "completed"
  | "failed"
  | "stopped";

export type SessionEvent =
  | { type: "stdout"; text: string }
  | { type: "stderr"; text: string }
  | { type: "status"; text: string }
  | { type: "question"; text: string }
  | { type: "artifact"; path: string }
  | { type: "exit"; code: number };
