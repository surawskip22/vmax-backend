import type { ReactNode } from "react";
import { Icon } from "./icons";
import { BottomNav, RoleAwareSidebar } from "./RoleAwareSidebar";
import type { User } from "./types";

export function AppShell({
  user,
  active,
  children,
  onNavigate,
  onLogout,
  queueCount,
  logo,
  compactLogo,
}: {
  user: User;
  active: string;
  children: ReactNode;
  onNavigate: (section: string) => void;
  onLogout: () => void;
  queueCount: number;
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
        {children}
        <BottomNav user={user} active={active} onNavigate={onNavigate} />
      </div>
    </div>
  );
}
