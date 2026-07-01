import type { ReactNode } from "react";
import { Icon } from "./icons";
import { isCompanyWorker, isIndependentContractor, isInvestor } from "./access";
import { peopleLabelsForUser, profileLabels } from "./roleLabels";
import type { User } from "./types";

type SectionId = "home" | "projects" | "reports" | "team" | "portfolio" | "settings";
type NavItem = {
  id: SectionId;
  label: string;
  icon: Parameters<typeof Icon>[0]["name"];
};

function getNavigationForUser(user: User): NavItem[] {
  if (isCompanyWorker(user)) {
    return [
      { id: "projects", label: "Moje zlecenia", icon: "clipboard" },
      { id: "settings", label: "Ustawienia", icon: "settings" },
    ];
  }
  if (isIndependentContractor(user)) {
    return [
      { id: "projects", label: "Moje zlecenia", icon: "clipboard" },
      { id: "reports", label: "Raporty", icon: "report" },
      { id: "portfolio", label: "Moja wizytówka", icon: "image" },
      { id: "settings", label: "Ustawienia", icon: "settings" },
    ];
  }
  if (isInvestor(user)) {
    return [
      { id: "projects", label: "Inwestycje / Zlecenia", icon: "clipboard" },
      { id: "team", label: peopleLabelsForUser(user).section, icon: "users" },
      { id: "reports", label: "Raporty", icon: "report" },
      { id: "settings", label: "Ustawienia", icon: "settings" },
    ];
  }
  return [
    { id: "home", label: "Pulpit", icon: "home" },
    { id: "projects", label: "Zlecenia", icon: "clipboard" },
    { id: "team", label: peopleLabelsForUser(user).section, icon: "users" },
    { id: "reports", label: "Raporty", icon: "report" },
    { id: "settings", label: "Ustawienia", icon: "settings" },
  ];
}

export function visibleSectionForUser(user: User, section: string): SectionId {
  const nav = getNavigationForUser(user);
  return nav.some((item) => item.id === section) ? (section as SectionId) : nav[0].id;
}

export function RoleAwareSidebar({
  user,
  active,
  onNavigate,
  onLogout,
  queueCount,
  logo,
}: {
  user: User;
  active: string;
  onNavigate: (section: string) => void;
  onLogout: () => void;
  queueCount: number;
  logo: ReactNode;
}) {
  const nav = getNavigationForUser(user);
  return (
    <aside className="sidebar">
      {logo}
      <div className="role-badge">
        <small>Typ konta</small>
        <strong>{user.profile_type ? profileLabels[user.profile_type] : "Nie wybrano"}</strong>
      </div>
      <nav>
        {nav.map(({ id, label, icon }) => (
          <button className={active === id ? "active" : ""} onClick={() => onNavigate(id)} key={id}>
            <Icon name={icon} /><span>{label}</span>
          </button>
        ))}
      </nav>
      <div className="sidebar__bottom">
        {queueCount > 0 && <div className="sync-pill"><Icon name="sync" /> {queueCount} czeka</div>}
        <button className="profile-button" onClick={() => onNavigate("settings")}>
          <span>{(user.name || user.email).slice(0, 2).toUpperCase()}</span>
          <div><b>{user.name || "Użytkownik"}</b><small>{user.email}</small></div>
        </button>
        <button className="logout-button" onClick={onLogout}>Wyloguj się</button>
      </div>
    </aside>
  );
}

export function BottomNav({
  user,
  active,
  onNavigate,
}: {
  user: User;
  active: string;
  onNavigate: (section: string) => void;
}) {
  const nav = getNavigationForUser(user);
  return (
    <nav className={`bottom-nav bottom-nav--${nav.length}`}>
      {nav.slice(0, 4).map(({ id, label, icon }) => (
        <button className={active === id ? "active" : ""} onClick={() => onNavigate(id)} key={id}>
          <Icon name={icon} /><span>{label}</span>
        </button>
      ))}
    </nav>
  );
}
