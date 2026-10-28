// Thin fetch helpers. Payloads are JSON straight from the FastAPI backend.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

export class ApiError extends Error {
  status: number;
  body: Json;
  constructor(status: number, body: Json) {
    super(typeof body?.detail === "string" ? body.detail : `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

async function request<T = Json>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data);
  return data as T;
}

export const api = {
  get: <T = Json>(path: string) => request<T>("GET", path),
  post: <T = Json>(path: string, body: unknown = {}) => request<T>("POST", path, body),
  put: <T = Json>(path: string, body: unknown) => request<T>("PUT", path, body),
  del: <T = Json>(path: string) => request<T>("DELETE", path),
};

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}
