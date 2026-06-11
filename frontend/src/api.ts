export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
  guestToken?: string,
): Promise<T> {
  const headers = new Headers(options.headers);
  if (guestToken) headers.set("x-guest-token", guestToken);
  if (options.body && !(options.body instanceof FormData)) {
    headers.set("content-type", "application/json");
  }
  const response = await fetch(`/api${path}`, {
    ...options,
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    let detail = "Wystąpił błąd";
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      detail = response.statusText || detail;
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export function jsonBody(value: unknown): RequestInit {
  return { body: JSON.stringify(value) };
}
