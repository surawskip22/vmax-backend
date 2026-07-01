import { useCallback, useEffect, useState } from "react";
import type { User } from "./types";

export type UiMode = "simple" | "advanced";

const STORAGE_PREFIX = "panmajster:viewMode";

function defaultUiMode(user?: User | null): UiMode {
  return user?.preferred_mode === "expanded" ? "advanced" : "simple";
}

function userStorageKey(user?: User | null): string | null {
  if (!user) return null;
  if (user.id) return `${STORAGE_PREFIX}:${user.id}`;
  const email = user.email?.trim().toLowerCase();
  if (email) return `${STORAGE_PREFIX}:${user.profile_type || "user"}:${email}`;
  return null;
}

function readStoredUiMode(key: string | null, fallback: UiMode): UiMode {
  if (typeof window === "undefined") return "simple";
  if (!key) return fallback;
  try {
    return window.localStorage.getItem(key) === "advanced" ? "advanced" : fallback;
  } catch {
    return fallback;
  }
}

export function useUiMode(user?: User | null): [UiMode, (mode: UiMode) => void] {
  const storageKey = userStorageKey(user);
  const fallbackMode = defaultUiMode(user);
  const [mode, setMode] = useState<UiMode>(() => readStoredUiMode(storageKey, fallbackMode));

  const updateMode = useCallback((next: UiMode) => {
    setMode(next);
    if (!storageKey) return;
    try {
      window.localStorage.setItem(storageKey, next);
    } catch {
      // Tryb nadal dziala w tej sesji, nawet jesli przegladarka blokuje zapis.
    }
  }, [storageKey]);

  useEffect(() => {
    setMode(readStoredUiMode(storageKey, fallbackMode));
  }, [fallbackMode, storageKey]);

  useEffect(() => {
    if (!storageKey) return undefined;
    function syncMode(event: StorageEvent) {
      if (event.key === storageKey) setMode(readStoredUiMode(storageKey, fallbackMode));
    }
    window.addEventListener("storage", syncMode);
    return () => window.removeEventListener("storage", syncMode);
  }, [fallbackMode, storageKey]);

  return [mode, updateMode];
}
