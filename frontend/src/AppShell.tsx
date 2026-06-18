import type { ReactNode } from "react";
import { Icon } from "./icons";
import { BottomNav, RoleAwareSidebar } from "./RoleAwareSidebar";
import type { User } from "./types";
import type { UiMode } from "./useUiMode";

export function AppShell({
  user,
  active,
  children,
  onNavigate,
  onLogout,
  queueCount,
  uiMode,
  onUiModeChange,
  logo,
  compactLogo,
}: {
  user: User;
  active: string;
  children: ReactNode;
  onNavigate: (section: string) => void;
  onLogout: () => void;
  queueCount: number;
  uiMode: UiMode;
  onUiModeChange: (mode: UiMode) => void;
  logo: ReactNode;
  compactLogo: ReactNode;
}) {
  return (
    <div className="app-shell">
      <RoleAwareSidebar
        user={user}
        active={active}
        onNavigate={onNavigate}
        onLogout={onLogout}
        queueCount={queueCount}
        logo={logo}
      />
      <div className="app-main">
        <header className="mobile-appbar">
          {compactLogo}
          <div className="mobile-appbar__actions">
            {queueCount > 0 && <span className="queue-badge">{queueCount}</span>}
            <button className="icon-button" onClick={() => onNavigate("settings")}><Icon name="menu" /></button>
          </div>
        </header>
        <div className="ui-mode-bar" aria-label="Tryb pracy aplikacji">
          <div>
            <strong>{uiMode === "simple" ? "Tryb prosty" : "Tryb rozbudowany"}</strong>
            <small>{uiMode === "simple" ? "Szybka praca w terenie" : "Pełne dane i zarządzanie"}</small>
          </div>
          <div className="ui-mode-switch" role="group" aria-label="Przełącz tryb widoku">
            <button
              type="button"
              className={uiMode === "simple" ? "active" : ""}
              onClick={() => onUiModeChange("simple")}
            >
              Prosty
            </button>
            <button
              type="button"
              className={uiMode === "advanced" ? "active" : ""}
              onClick={() => onUiModeChange("advanced")}
            >
              Rozbudowany
            </button>
          </div>
        </div>
        {children}
        <BottomNav user={user} active={active} onNavigate={onNavigate} />
      </div>
    </div>
  );
}
