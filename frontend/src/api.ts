let token = "";
export async function bootstrap() {
  const response = await fetch("/api/v1/bootstrap", {
    credentials: "same-origin",
  });
  if (!response.ok)
    throw new Error(
      (await response.json()).detail ||
        "Could not connect to the local service.",
    );
  token = (await response.json()).token;
}

export async function api<T>(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Local-Token", token);
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const response = await fetch("/api/v1" + path, {
    ...init,
    headers,
    credentials: "same-origin",
  });
  if (response.status === 401 && retry) {
    await bootstrap();
    return api<T>(path, init, false);
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : `Request failed (${response.status}).`,
    );
  }
  return response.json();
}
export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
export const patch = <T>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const remove = <T>(path: string) => api<T>(path, { method: "DELETE" });
