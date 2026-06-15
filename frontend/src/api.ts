export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function formatErrorDetail(value: unknown): string {
  if (!value) return "Wystąpił błąd";
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          const location = "loc" in item && Array.isArray(item.loc) ? item.loc.join(".") : "";
          return [location, String(item.msg)].filter(Boolean).join(": ");
        }
        return JSON.stringify(item);
      })
      .join(", ");
  }
  if (typeof value === "object" && "message" in value) return String(value.message);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
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
      detail = formatErrorDetail(payload.detail || payload);
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
