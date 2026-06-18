import { useCallback, useEffect, useState } from "react";

export type UiMode = "simple" | "advanced";

const STORAGE_KEY = "panmajster.uiMode";

function readStoredUiMode(): UiMode {
  if (typeof window === "undefined") return "simple";
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "advanced" ? "advanced" : "simple";
  } catch {
    return "simple";
  }
}

export function useUiMode(): [UiMode, (mode: UiMode) => void] {
  const [mode, setMode] = useState<UiMode>(readStoredUiMode);

  const updateMode = useCallback((next: UiMode) => {
    setMode(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Tryb nadal dziala w tej sesji, nawet jesli przegladarka blokuje zapis.
    }
  }, []);

  useEffect(() => {
    function syncMode(event: StorageEvent) {
      if (event.key === STORAGE_KEY) setMode(readStoredUiMode());
    }
    window.addEventListener("storage", syncMode);
    return () => window.removeEventListener("storage", syncMode);
  }, []);

  return [mode, updateMode];
}
