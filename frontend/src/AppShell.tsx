import { useState, type ReactNode } from "react";
import { Icon } from "./icons";
import { BottomNav, getNavigationForUser, RoleAwareSidebar } from "./RoleAwareSidebar";
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
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const nav = getNavigationForUser(user);
  const activeLabel = nav.find((item) => item.id === active)?.label || nav[0]?.label || "Pulpit";

  function navigateFromMobile(section: string) {
    setMobileMenuOpen(false);
    onNavigate(section);
  }

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
          <div className="mobile-appbar__identity">
            {compactLogo}
            <strong className="mobile-appbar__title">{activeLabel}</strong>
          </div>
          <div className="mobile-appbar__actions">
            {queueCount > 0 && <span className="queue-badge">{queueCount}</span>}
            <button
              type="button"
              className="icon-button"
              aria-label="Otwórz menu"
              aria-expanded={mobileMenuOpen}
              onClick={() => setMobileMenuOpen((open) => !open)}
            >
              <Icon name="menu" />
            </button>
          </div>
        </header>
        {mobileMenuOpen && (
          <>
            <button
              type="button"
              className="mobile-menu-backdrop"
              aria-label="Zamknij menu"
              onClick={() => setMobileMenuOpen(false)}
            />
            <nav className="mobile-menu-panel" aria-label="Menu aplikacji">
              <header>
                <strong>Menu</strong>
                <button type="button" className="icon-button" aria-label="Zamknij menu" onClick={() => setMobileMenuOpen(false)}>
                  <Icon name="close" />
                </button>
              </header>
              <div>
                {nav.map(({ id, label, icon }) => (
                  <button
                    type="button"
                    className={active === id ? "active" : ""}
                    onClick={() => navigateFromMobile(id)}
                    key={id}
                  >
                    <Icon name={icon} />
                    <span>{label}</span>
                  </button>
                ))}
              </div>
              <button type="button" className="logout-button" onClick={() => { setMobileMenuOpen(false); onLogout(); }}>
                Wyloguj się
              </button>
            </nav>
          </>
        )}
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
