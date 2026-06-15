import {
  FormEvent,
  ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { api } from "./api";
import { Icon } from "./icons";
import {
  deleteQueuedEntry,
  queueEntry,
  queuedEntries,
  type QueuedEntry,
} from "./offline";
import type { ClientLink, Entry, Project, Report, User, WorkerProfile, Workspace } from "./types";

type Toast = { kind: "success" | "error" | "info"; message: string };
const profileLabels: Record<NonNullable<User["profile_type"]>, string> = {
  company_owner: "Szef firmy",
  investor: "Inwestor",
  independent_contractor: "Samodzielny majster",
  company_worker: "Majster - członek firmy",
  worker: "Majster - członek firmy",
};
const testAccounts = [
  { label: "Szef firmy", email: "szef@majster.pl" },
  { label: "Inwestor", email: "inwestor@majster.pl" },
  { label: "Samodzielny majster", email: "samodzielny@majster.pl" },
  { label: "Majster - członek firmy", email: "pracownik@majster.pl" },
];

function isCompanyWorker(user?: User): boolean {
  return user?.profile_type === "company_worker" || user?.profile_type === "worker";
}

function isCompanyOwner(user?: User): boolean {
  return user?.profile_type === "company_owner";
}

function isInvestor(user?: User): boolean {
  return user?.profile_type === "investor";
}

function isIndependentContractor(user?: User): boolean {
  return user?.profile_type === "independent_contractor";
}

function canManagePeople(user?: User): boolean {
  return Boolean(user && !isIndependentContractor(user) && !isCompanyWorker(user));
}

function canCreateProject(user?: User): boolean {
  return Boolean(user && !isCompanyWorker(user));
}

function canSeeTeamPanel(user?: User): boolean {
  return canManagePeople(user);
}

function workerKindLabel(worker: WorkerProfile): string {
  return worker.profile_kind === "crew" ? "Ekipa" : "Majster";
}

function workerAccountLabel(worker: WorkerProfile): string {
  if (!worker.active) return "dezaktywowany";
  if (worker.profile_kind === "crew" && !worker.email) return "ekipa link-only / bez e-maila";
  if (worker.account_status === "active") return "konto aktywne";
  if (worker.account_status === "pending_email") return "oczekuje na potwierdzenie e-mail";
  if (worker.account_type === "account") return "konto stałe / e-mail";
  return "link-only";
}

function formString(data: FormData, name: string): string {
  return String(data.get(name) || "").trim();
}

async function copyToClipboard(value: string): Promise<boolean> {
  try {
    if (!navigator.clipboard) return false;
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}

type Route =
  | { kind: "marketing" }
  | { kind: "app" }
  | { kind: "guest"; token: string }
  | { kind: "client"; token: string }
  | { kind: "invite"; token: string }
  | { kind: "report"; token: string }
  | { kind: "portfolio"; slug: string };

const statusLabels: Record<string, string> = {
  assigned: "Zlecone",
  in_progress: "W realizacji",
  completed: "Zakończono",
  active: "W realizacji",
  planned: "Planowany",
};

function route(): Route {
  const matchGuest = location.pathname.match(/^\/g\/([^/]+)/);
  if (matchGuest) return { kind: "guest", token: matchGuest[1] };
  const matchClient = location.pathname.match(/^\/c\/([^/]+)/);
  if (matchClient) return { kind: "client", token: matchClient[1] };
  const matchInvite = location.pathname.match(/^\/invite\/([^/]+)/);
  if (matchInvite) return { kind: "invite", token: matchInvite[1] };
  const matchReport = location.pathname.match(/^\/r\/([^/]+)/);
  if (matchReport) return { kind: "report", token: matchReport[1] };
  const matchPortfolio = location.pathname.match(/^\/portfolio\/([^/]+)/);
  if (matchPortfolio) return { kind: "portfolio", slug: matchPortfolio[1] };
  if (location.pathname.startsWith("/app")) return { kind: "app" };
  return { kind: "marketing" };
}

function navigate(path: string) {
  history.pushState({}, "", path);
  dispatchEvent(new PopStateEvent("popstate"));
}

function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <button className={`brand ${compact ? "brand--compact" : ""}`} onClick={() => navigate("/")}>
      <img src={compact ? "/brand/symbol.png" : "/brand/logo.png"} alt="Pan Majster" />
    </button>
  );
}

function Button({
  children,
  variant = "primary",
  icon,
  busy,
  className = "",
  ...props
}: {
  children: ReactNode;
  variant?: "primary" | "secondary" | "ghost" | "danger";
  icon?: Parameters<typeof Icon>[0]["name"];
  busy?: boolean;
  className?: string;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      className={`button button--${variant} ${className}`}
      disabled={busy || props.disabled}
      {...props}
    >
      {busy ? <span className="spinner" /> : icon ? <Icon name={icon} size={20} /> : null}
      {children}
    </button>
  );
}

function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  useEffect(() => {
    const close = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    addEventListener("keydown", close);
    return () => removeEventListener("keydown", close);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <section
        className={`modal ${wide ? "modal--wide" : ""}`}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="modal__header">
          <h2>{title}</h2>
          <button className="icon-button" onClick={onClose} aria-label="Zamknij">
            <Icon name="close" />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}

function Marketing({ onLogin }: { onLogin: () => void }) {
  const branches = [
    ["Remonty", "Dokumentuj postęp i dodatkowe prace."],
    ["Serwis", "Pokaż usterkę, naprawę i efekt."],
    ["Montaż", "Zapisuj stan przed, przebieg i odbiór."],
    ["Ogrody", "Buduj czytelne historie przed i po."],
    ["Sprzątanie", "Potwierdzaj wykonanie usługi zdjęciami."],
    ["Inwestor", "Śledź etapy bez codziennych telefonów."],
  ];
  return (
    <div className="marketing">
      <nav className="marketing-nav">
        <Logo />
        <div className="marketing-nav__links">
          <a href="#jak-dziala">Jak działa</a>
          <a href="#dla-kogo">Dla kogo</a>
          <a href="#cennik">Cennik</a>
          <Button variant="secondary" onClick={onLogin}>Zaloguj się</Button>
          <Button onClick={onLogin}>Chcę przetestować</Button>
        </div>
      </nav>
      <main>
        <section className="hero">
          <div className="hero__copy">
            <span className="eyebrow">Prosty raportownik pracy z telefonu</span>
            <h1>
              Pokaż robotę.<br />
              <span>Nie tłumacz się.</span>
            </h1>
            <p>
              Zrób zdjęcie, powiedz co zrobione i wyślij klientowi lub szefowi
              czytelny raport. Cała historia pracy zostaje w jednym miejscu.
            </p>
            <div className="hero__actions">
              <Button onClick={onLogin} icon="send">Uruchom wersję testową</Button>
              <a className="button button--secondary" href="#jak-dziala">
                Zobacz, jak działa
              </a>
            </div>
            <div className="hero__trust">
              <span><Icon name="check" size={17} /> Bez karty</span>
              <span><Icon name="check" size={17} /> Działa na telefonie</span>
              <span><Icon name="check" size={17} /> Dane w UE</span>
            </div>
          </div>
          <div className="hero__visual">
            <img className="hero-characters" src="/brand/hero-characters.png" alt="Majster, ogrodnik i inwestor korzystający z Pan Majster" />
            <div className="phone-demo">
              <div className="phone-demo__top">
                <img src="/brand/symbol.png" alt="" />
                <strong>Pan Majster</strong>
              </div>
              <div className="phone-demo__project">
                <small>BIEŻĄCE ZLECENIE</small>
                <h3>Remont łazienki</h3>
                <p>ul. Kwiatowa 15, Kraków</p>
                <span className="status status--active">● W trakcie</span>
              </div>
              <div className="field-actions field-actions--demo">
                <div><Icon name="camera" /><b>Zrób zdjęcie</b></div>
                <div><Icon name="mic" /><b>Nagraj opis</b></div>
                <div><Icon name="alert" /><b>Problem</b></div>
                <div><Icon name="send" /><b>Wyślij raport</b></div>
              </div>
            </div>
            <div className="floating-card floating-card--one">
              <Icon name="check" />
              <div><b>Raport gotowy</b><small>12 zdjęć · 3 etapy</small></div>
            </div>
            <div className="floating-card floating-card--two">
              <Icon name="sync" />
              <div><b>Synchronizacja OK</b><small>Wszystko zapisane</small></div>
            </div>
          </div>
        </section>

        <section className="how" id="jak-dziala">
          <span className="eyebrow">Raport w 60 sekund</span>
          <h2>Minimum pisania. Maksimum konkretu.</h2>
          <div className="step-grid">
            {[
              ["camera", "1. Zdjęcia", "Dodaj zdjęcia postępu, efektu albo usterki."],
              ["mic", "2. Głos lub tekst", "Powiedz krótko, co zostało wykonane."],
              ["report", "3. Raport", "System porządkuje materiał według etapów."],
              ["link", "4. Link / QR / PDF", "Klient otwiera raport bez instalowania aplikacji."],
            ].map(([icon, title, text]) => (
              <article className="step-card" key={title}>
                <span><Icon name={icon as Parameters<typeof Icon>[0]["name"]} size={32} /></span>
                <h3>{title}</h3>
                <p>{text}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="audiences" id="dla-kogo">
          <div>
            <span className="eyebrow">Jedna aplikacja. Wiele ekip.</span>
            <h2>Ten sam prosty proces w każdej branży.</h2>
          </div>
          <div className="audience-grid">
            {branches.map(([title, text], index) => (
              <article key={title}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <h3>{title}</h3>
                <p>{text}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="pricing" id="cennik">
          <div>
            <span className="eyebrow">Wersja testowa</span>
            <h2>Przetestuj z prawdziwą ekipą.</h2>
            <p>
              Pierwsi użytkownicy otrzymują bezpłatny dostęp pilotażowy. Docelowy
              abonament planujemy od 15 EUR miesięcznie.
            </p>
          </div>
          <Button onClick={onLogin} icon="send">Dołącz do testów</Button>
        </section>
      </main>
      <footer className="marketing-footer">
        <Logo />
        <p>Zdjęcie. Głos. Raport.</p>
      </footer>
    </div>
  );
}

function AuthModal({
  onClose,
  onSuccess,
  initialEmail = "",
}: {
  onClose: () => void;
  onSuccess: (user: User) => void;
  initialEmail?: string;
}) {
  const [step, setStep] = useState<"email" | "code" | "password">("email");
  const [email, setEmail] = useState(initialEmail);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [devCode, setDevCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function requestCode(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api<{ dev_code?: string }>("/auth/request-code", {
        method: "POST",
        body: JSON.stringify({ email }),
      });
      setDevCode(result.dev_code || "");
      setStep("code");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Nie udało się wysłać kodu");
    } finally {
      setBusy(false);
    }
  }

  async function verify(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await api<{ user: User }>("/auth/verify", {
        method: "POST",
        body: JSON.stringify({ email, code }),
      });
      onSuccess(result.user);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Nieprawidłowy kod");
    } finally {
      setBusy(false);
    }
  }

  async function loginWithPassword(event: FormEvent) {
    event.preventDefault();
    await loginWithCredentials(email, password);
  }

  async function loginWithCredentials(nextEmail: string, nextPassword: string) {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ user: User }>("/auth/password", {
        method: "POST",
        body: JSON.stringify({ email: nextEmail, password: nextPassword }),
      });
      onSuccess(result.user);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Nieprawidłowy email albo hasło");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={step === "email" ? "Wejdź do Pan Majster" : step === "password" ? "Logowanie testowe" : "Sprawdź pocztę"} onClose={onClose}>
      {step === "email" ? (
        <form className="form-stack" onSubmit={requestCode}>
          <p className="form-intro">Bez hasła. Wyślemy Ci jednorazowy kod logowania.</p>
          <label>
            Adres e-mail
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
          </label>
          {error && <p className="form-error">{error}</p>}
          <Button type="submit" busy={busy}>Wyślij kod</Button>
          <Button type="button" variant="ghost" onClick={() => setStep("password")}>Mam hasło testowe</Button>
          <div className="test-login-panel">
            <strong>Konta testowe lokalnie</strong>
            {testAccounts.map((account) => (
              <button
                type="button"
                key={account.email}
                onClick={() => loginWithCredentials(account.email, "test1234")}
                disabled={busy}
              >
                Zaloguj jako: {account.label}
                <small>{account.email}</small>
              </button>
            ))}
          </div>
          <small>Logując się, akceptujesz warunki wersji testowej.</small>
        </form>
      ) : step === "password" ? (
        <form className="form-stack" onSubmit={loginWithPassword}>
          <p className="form-intro">Tylko lokalne konta developerskie. Zaproszeni wykonawcy z emailem nadal potwierdzają konto kodem z maila.</p>
          <label>
            Adres e-mail
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
          </label>
          <label>
            Hasło
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </label>
          {error && <p className="form-error">{error}</p>}
          <Button type="submit" busy={busy}>Zaloguj hasłem</Button>
          <Button type="button" variant="ghost" onClick={() => setStep("email")}>Wróć do kodu e-mail</Button>
        </form>
      ) : (
        <form className="form-stack" onSubmit={verify}>
          <p className="form-intro">
            Wpisz kod wysłany na <strong>{email}</strong>.
          </p>
          {devCode && <div className="dev-code">Kod lokalny: <strong>{devCode}</strong></div>}
          <label>
            Kod 6-cyfrowy
            <input
              className="otp-input"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]{6}"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              required
              autoFocus
            />
          </label>
          {error && <p className="form-error">{error}</p>}
          <Button type="submit" busy={busy}>Zaloguj się</Button>
          <Button type="button" variant="ghost" onClick={() => setStep("email")}>Zmień adres</Button>
        </form>
      )}
    </Modal>
  );
}

function Onboarding({
  onComplete,
  onBack,
}: {
  onComplete: (user: User) => void;
  onBack: () => void;
}) {
  const [selected, setSelected] = useState<
    "company_owner" | "independent_contractor" | "investor" | "company_worker" | null
  >(null);
  const [companyName, setCompanyName] = useState("");
  const [mode] = useState<"expanded" | "field">("expanded");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const profiles: Array<{
    id: "company_owner" | "independent_contractor" | "investor" | "company_worker";
    icon: Parameters<typeof Icon>[0]["name"];
    title: string;
    text: string;
  }> = [
    {
      id: "company_owner" as const,
      icon: "users" as const,
      title: "Jestem szefem firmy",
      text: "Dodaję majstrów, przydzielam im zlecenia i sprawdzam postęp prac.",
    },
    {
      id: "independent_contractor" as const,
      icon: "clipboard" as const,
      title: "Jestem samodzielnym majstrem",
      text: "Prowadzę własne zlecenia, robię zdjęcia i wysyłam klientom raporty.",
    },
    {
      id: "investor" as const,
      icon: "home" as const,
      title: "Jestem inwestorem",
      text: "Zlecam prace ekipom i chcę widzieć zdjęcia, postęp oraz raporty.",
    },
  ];

  profiles.push({
    id: "company_worker" as const,
    icon: "users" as const,
    title: "Jestem majstrem - członkiem firmy",
    text: "Mam konto stałe w firmie szefa i widzę zlecenia przypisane do mnie albo mojej ekipy.",
  });

  async function submit() {
    if (!selected) return;
    setBusy(true);
    setError("");
    try {
      const user = await api<User>("/onboarding", {
        method: "POST",
        body: JSON.stringify({
          profile_type: selected,
          preferred_mode: mode,
          company_name: selected === "company_owner" ? companyName : null,
        }),
      });
      onComplete(user);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Nie udało się zapisać profilu");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="onboarding">
      <div className="onboarding__card">
        <Logo />
        <span className="eyebrow">Pierwsze uruchomienie</span>
        <h1>Jak chcesz korzystać z Pan Majster?</h1>
        <p className="onboarding__intro">
          Wybierz najbliższy opis. Pokażemy Ci tylko funkcje potrzebne na start.
        </p>
        <div className="profile-choice">
          {profiles.map((profile) => (
            <button
              type="button"
              className={selected === profile.id ? "active" : ""}
              onClick={() => setSelected(profile.id)}
              key={profile.id}
            >
              <span><Icon name={profile.icon} size={32} /></span>
              <strong>{profile.title}</strong>
              <small>{profile.text}</small>
            </button>
          ))}
        </div>
        {selected === "company_owner" && (
          <label className="onboarding__field">
            Nazwa firmy
            <input
              value={companyName}
              onChange={(event) => setCompanyName(event.target.value)}
              placeholder="np. Kowalski Remonty"
              autoFocus
            />
          </label>
        )}
        {selected === "independent_contractor" && (
          <p className="form-intro">Tryb terenowy/prosty przełączysz później u góry widoku zlecenia.</p>
        )}
        {selected === "company_worker" && (
          <p className="form-intro">
            Założyłeś konto jako Majster - członek firmy. Aby widzieć zlecenia, musisz zostać przypisany do firmy przez szefa.
          </p>
        )}
        {error && <p className="form-error">{error}</p>}
        <Button
          onClick={submit}
          busy={busy}
          disabled={!selected || (selected === "company_owner" && !companyName.trim())}
        >
          Przejdź do aplikacji
        </Button>
        <Button type="button" variant="ghost" onClick={onBack}>Wstecz / zmień konto</Button>
      </div>
    </div>
  );
}

function CreateProjectModal({
  user,
  onClose,
  onCreated,
}: {
  user: User;
  onClose: () => void;
  onCreated: (project: Project) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [workers, setWorkers] = useState<WorkerProfile[]>([]);
  const projectLabel = user.profile_type === "investor" ? "inwestycję" : "zlecenie";

  useEffect(() => {
    if (!canSeeTeamPanel(user)) return;
    api<WorkerProfile[]>("/workers").then(setWorkers).catch(() => setWorkers([]));
  }, [user]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const data = new FormData(event.currentTarget);
    const investorMode = isInvestor(user);
    try {
      const project = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          name: formString(data, "name"),
          client_name: investorMode ? "" : formString(data, "client_name"),
          client_email: investorMode ? "" : formString(data, "client_email"),
          address: formString(data, "address"),
          description: formString(data, "description"),
          template: formString(data, "template"),
          workspace_id: investorMode ? null : formString(data, "workspace_id") || null,
          worker_profile_id: formString(data, "worker_profile_id") || null,
        }),
      });
      onCreated(project);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Nie udało się utworzyć projektu");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title={user.profile_type === "investor" ? "Nowa inwestycja" : "Nowe zlecenie"} onClose={onClose}>
      <form className="form-stack" onSubmit={submit}>
        <label>Nazwa {projectLabel}<input name="name" placeholder={user.profile_type === "investor" ? "np. Budowa domu - etap instalacji" : "np. Remont łazienki"} required autoFocus /></label>
        {!isInvestor(user) && (
          <div className="form-row">
            <label>Klient<input name="client_name" placeholder="Jan Kowalski" /></label>
            <label>E-mail klienta<input type="email" name="client_email" placeholder="Opcjonalnie" /></label>
          </div>
        )}
        {canSeeTeamPanel(user) && (
          <label>
            {isInvestor(user) ? "Wykonawca" : "Majster / ekipa"}
            <select name="worker_profile_id" defaultValue="">
              <option value="">Wybiorę później</option>
              {workers.map((worker) => (
                <option value={worker.id} key={worker.id}>
                  {workerKindLabel(worker)}: {worker.label} - {workerAccountLabel(worker)}
                </option>
              ))}
            </select>
          </label>
        )}
        <label>Adres<input name="address" placeholder="ul. Kwiatowa 15, Kraków" /></label>
        <label>
          Szablon etapów
          <select name="template" defaultValue="remont">
            <option value="remont">Remont</option>
            <option value="serwis">Serwis i naprawa</option>
            <option value="montaz">Montaż</option>
            <option value="ogrod">Ogród</option>
            <option value="sprzatanie">Sprzątanie</option>
            <option value="techniczne">Prace techniczne</option>
            <option value="custom">Uniwersalny</option>
          </select>
        </label>
        {!isInvestor(user) && user.workspaces.length > 0 && (
          <label>
            Firma
            <select name="workspace_id" defaultValue="">
              <option value="">Projekt prywatny</option>
              {user.workspaces.map((workspace) => (
                <option value={workspace.id} key={workspace.id}>{workspace.name}</option>
              ))}
            </select>
          </label>
        )}
        <label>Opis<textarea name="description" rows={3} placeholder="Krótki zakres prac..." /></label>
        {error && <p className="form-error">{error}</p>}
        <Button type="submit" busy={busy} icon="plus">Utwórz {projectLabel}</Button>
      </form>
    </Modal>
  );
}

function Shell({
  user,
  active,
  children,
  onNavigate,
  onLogout,
  queueCount,
}: {
  user: User;
  active: string;
  children: ReactNode;
  onNavigate: (section: string) => void;
  onLogout: () => void;
  queueCount: number;
}) {
  const nav = [
    ["home", "Pulpit", "home"],
    ["projects", "Zlecenia", "clipboard"],
    ["reports", "Raporty", "report"],
    ...(!canSeeTeamPanel(user)
      ? []
      : ([["team", user.profile_type === "investor" ? "Wykonawcy" : "Zespół", "users"]] as const)),
    ["settings", "Ustawienia", "settings"],
  ] as const;
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Logo />
        <div className="role-badge">
          <small>Typ konta</small>
          <strong>{user.profile_type ? profileLabels[user.profile_type] : "Nie wybrano"}</strong>
        </div>
        <nav>
          {nav.map(([id, label, icon]) => (
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
      <div className="app-main">
        <header className="mobile-appbar">
          <Logo compact />
          <div className="mobile-appbar__actions">
            {queueCount > 0 && <span className="queue-badge">{queueCount}</span>}
            <button className="icon-button" onClick={() => onNavigate("settings")}><Icon name="menu" /></button>
          </div>
        </header>
        {children}
        <nav className="bottom-nav">
          {nav.slice(0, 4).map(([id, label, icon]) => (
            <button className={active === id ? "active" : ""} onClick={() => onNavigate(id)} key={id}>
              <Icon name={icon} /><span>{label}</span>
            </button>
          ))}
        </nav>
      </div>
    </div>
  );
}

function Dashboard({
  user,
  projects,
  onProject,
  onCreate,
}: {
  user: User;
  projects: Project[];
  onProject: (project: Project) => void;
  onCreate: () => void;
}) {
  const active = projects.filter((project) => ["assigned", "in_progress"].includes(project.status));
  const problems = projects.reduce((sum, item) => sum + (item.open_problem_count || 0), 0);
  const canCreate = canCreateProject(user);
  const intro =
    isInvestor(user)
      ? "Tu widzisz postęp inwestycji, raporty i sprawy wymagające decyzji."
      : isCompanyOwner(user)
        ? "Tu kontrolujesz zlecenia firmy, majstrów i zgłoszone problemy."
        : "Tu masz szybki podgląd swoich zleceń i raportów.";
  const createLabel = user.profile_type === "investor" ? "Dodaj inwestycję" : "Dodaj zlecenie";
  return (
    <div className="page dashboard">
      <header className="page-header">
        <div>
          <span className="eyebrow">Środa, {new Intl.DateTimeFormat("pl", { day: "numeric", month: "long" }).format(new Date())}</span>
          <div className="role-inline">Typ konta: <strong>{user.profile_type ? profileLabels[user.profile_type] : "Nie wybrano"}</strong></div>
          <h1>Dzień dobry{user.name ? `, ${user.name.split(" ")[0]}` : ""}!</h1>
          <p>{intro}</p>
        </div>
        {canCreate && <Button icon="plus" onClick={onCreate}>{createLabel}</Button>}
      </header>
      <div className="stat-grid">
        <article><span className="stat-icon stat-icon--blue"><Icon name="clipboard" /></span><div><small>Aktywne zlecenia</small><strong>{active.length}</strong></div></article>
        <article><span className="stat-icon stat-icon--red"><Icon name="alert" /></span><div><small>Otwarte problemy</small><strong>{problems}</strong></div></article>
        <article><span className="stat-icon stat-icon--green"><Icon name="check" /></span><div><small>Zakończone</small><strong>{projects.filter((p) => p.status === "completed").length}</strong></div></article>
        <article><span className="stat-icon stat-icon--orange"><Icon name="users" /></span><div><small>Wszystkie projekty</small><strong>{projects.length}</strong></div></article>
      </div>
      <section className="panel">
        <div className="panel__header">
          <div><h2>Ostatnie zlecenia</h2><p>Wybierz projekt, aby dodać zdjęcia lub raport.</p></div>
          {canCreate && <button className="text-button" onClick={onCreate}>+ Nowe zlecenie</button>}
        </div>
        {projects.length === 0 ? (
          <EmptyState icon="clipboard" title="Dodaj pierwsze zlecenie" text="Projekt połączy zdjęcia, opisy, problemy i raporty w jedną historię.">
            {canCreate && <Button onClick={onCreate} icon="plus">Utwórz zlecenie</Button>}
          </EmptyState>
        ) : (
          <div className="project-list">
            {projects.slice(0, 8).map((project) => (
              <button key={project.id} className="project-row" onClick={() => onProject(project)}>
                <span className="project-row__icon"><Icon name="clipboard" /></span>
                <div className="project-row__main">
                  <strong>{project.name}</strong>
                  <span>{project.client_name || "Bez klienta"} · {project.address || "Bez adresu"}</span>
                </div>
                <span className={`status status--${project.status}`}>{statusLabels[project.status] || project.status}</span>
                <span className="project-row__meta">{project.role === "owner" ? "Właściciel" : "Współpraca"}</span>
                <Icon name="back" className="chevron" />
              </button>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function EmptyState({
  icon,
  title,
  text,
  children,
}: {
  icon: Parameters<typeof Icon>[0]["name"];
  title: string;
  text: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span><Icon name={icon} size={34} /></span>
      <h3>{title}</h3>
      <p>{text}</p>
      {children}
    </div>
  );
}

function ProjectsPage({
  user,
  projects,
  onProject,
  onCreate,
}: {
  user: User;
  projects: Project[];
  onProject: (project: Project) => void;
  onCreate: () => void;
}) {
  const [filter, setFilter] = useState("");
  const visible = projects.filter((item) =>
    `${item.name} ${item.client_name} ${item.address}`.toLowerCase().includes(filter.toLowerCase()),
  );
  return (
    <div className="page">
      <header className="page-header">
        <div><span className="eyebrow">Wszystkie realizacje</span><h1>Zlecenia</h1><p>Postęp, problemy i raporty w jednym miejscu.</p></div>
        {canCreateProject(user) && <Button icon="plus" onClick={onCreate}>Dodaj zlecenie</Button>}
      </header>
      <section className="panel">
        <div className="toolbar"><input type="search" placeholder="Szukaj zlecenia, klienta lub adresu..." value={filter} onChange={(e) => setFilter(e.target.value)} /></div>
        <div className="project-cards">
          {visible.map((project) => (
            <button className="project-card" key={project.id} onClick={() => onProject(project)}>
              <div className="project-card__top"><span className="project-card__icon"><Icon name="clipboard" /></span><span className={`status status--${project.status}`}>{statusLabels[project.status]}</span></div>
              <h3>{project.name}</h3>
              <p>{project.client_name || "Bez klienta"}</p>
              <span className="project-card__address">{project.address || "Adres nieuzupełniony"}</span>
              <div className="project-card__footer"><span>{project.entry_count || 0} wpisów</span><span>{project.open_problem_count || 0} problemów</span></div>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

function FieldAction({
  icon,
  title,
  subtitle,
  tone,
  onClick,
}: {
  icon: Parameters<typeof Icon>[0]["name"];
  title: string;
  subtitle: string;
  tone: string;
  onClick: () => void;
}) {
  return (
    <button className={`field-action field-action--${tone}`} onClick={onClick}>
      <span><Icon name={icon} size={38} /></span>
      <strong>{title}</strong>
      <small>{subtitle}</small>
    </button>
  );
}

function NewEntryModal({
  project,
  kind,
  mode: _mode,
  guestToken,
  onClose,
  onSaved,
  onQueued,
}: {
  project: Project;
  kind: "update" | "problem";
  mode: "photo" | "audio" | "text";
  guestToken?: string;
  onClose: () => void;
  onSaved: () => void;
  onQueued: () => void;
}) {
  const [body, setBody] = useState("");
  const [voiceNote, setVoiceNote] = useState("");
  const [stageId, setStageId] = useState(project.stages?.find((s) => s.status === "active")?.id || "");
  const [files, setFiles] = useState<File[]>([]);
  const [recordingTarget, setRecordingTarget] = useState<"description" | "note" | null>(null);
  const [transcribing, setTranscribing] = useState<"description" | "note" | null>(null);
  const [showVoiceNote, setShowVoiceNote] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  async function transcribe(file: File, target: "description" | "note") {
    setTranscribing(target);
    const data = new FormData();
    data.append("file", file);
    try {
      const result = await api<{ text: string }>(
        `/projects/${project.id}/transcribe`,
        { method: "POST", body: data },
        guestToken,
      );
      if (target === "description") {
        setBody((current) => [current, result.text].filter(Boolean).join(" "));
      } else {
        setVoiceNote((current) => [current, result.text].filter(Boolean).join("\n\n"));
      }
    } catch (reason) {
      setError(
        reason instanceof Error
          ? `${reason.message}. Nagranie nadal zostanie zapisane.`
          : "Nie udało się zamienić głosu na tekst. Nagranie nadal zostanie zapisane.",
      );
    } finally {
      setTranscribing(null);
    }
  }

  async function startRecording(target: "description" | "note") {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      chunks.current = [];
      mediaRecorder.ondataavailable = (event) => chunks.current.push(event.data);
      mediaRecorder.onstop = () => {
        const blob = new Blob(chunks.current, { type: mediaRecorder.mimeType || "audio/webm" });
        const prefix = target === "description" ? "opis" : "notatka";
        const file = new File([blob], `${prefix}-${Date.now()}.webm`, { type: blob.type });
        setFiles((current) => [...current, file]);
        stream.getTracks().forEach((track) => track.stop());
        void transcribe(file, target);
      };
      recorder.current = mediaRecorder;
      mediaRecorder.start();
      setRecordingTarget(target);
    } catch {
      setError("Przeglądarka nie udostępniła mikrofonu. Opis możesz wpisać ręcznie.");
    }
  }

  function stopRecording() {
    recorder.current?.stop();
    setRecordingTarget(null);
  }

  async function upload(entryId: string, selectedFiles: File[]) {
    for (const file of selectedFiles) {
      const data = new FormData();
      data.append("file", file);
      data.append("client_ref", crypto.randomUUID());
      data.append(
        "purpose",
        file.name.startsWith("opis-")
          ? "voice_description"
          : file.name.startsWith("notatka-")
            ? "voice_note"
            : "attachment",
      );
      await api(`/entries/${entryId}/media`, { method: "POST", body: data }, guestToken);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const clientRef = crypto.randomUUID();
    const payload = {
      kind,
      body,
      transcript: voiceNote,
      stage_id: stageId || null,
      client_ref: clientRef,
    };
    try {
      const entry = await api<Entry>(
        `/projects/${project.id}/entries`,
        { method: "POST", body: JSON.stringify(payload) },
        guestToken,
      );
      await upload(entry.id, files);
      onSaved();
    } catch (reason) {
      if (!navigator.onLine || reason instanceof TypeError) {
        const queued: QueuedEntry = {
          id: clientRef,
          projectId: project.id,
          guestToken,
          payload: { ...payload, stage_id: stageId || undefined },
          files: files.map((file) => ({
            name: file.name,
            type: file.type,
            blob: file,
            clientRef: crypto.randomUUID(),
          })),
          createdAt: Date.now(),
        };
        await queueEntry(queued);
        onQueued();
        return;
      }
      setError(reason instanceof Error ? reason.message : "Nie udało się zapisać wpisu");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={kind === "problem" ? "Zgłoś problem" : "Dodaj postęp prac"} onClose={onClose}>
      <form className="form-stack" onSubmit={submit}>
        {project.stages && project.stages.length > 0 && (
          <label>Etap<select value={stageId} onChange={(e) => setStageId(e.target.value)}><option value="">Bez etapu</option>{project.stages.map((stage) => <option value={stage.id} key={stage.id}>{stage.title}</option>)}</select></label>
        )}
        <label className="upload-zone">
          <Icon name="camera" size={34} />
          <strong>
            {stageId === project.stages?.[0]?.id
              ? "Zrób zdjęcie stanu przed pracą"
              : stageId === project.stages?.[2]?.id
                ? "Zrób zdjęcie efektu końcowego"
                : "Dodaj zdjęcia postępu prac"}
          </strong>
          <span>{files.filter((file) => file.type.startsWith("image/")).length ? `${files.filter((file) => file.type.startsWith("image/")).length} zdjęć wybranych` : "Możesz zrobić lub wybrać kilka zdjęć"}</span>
          <input type="file" accept="image/*" capture="environment" multiple onChange={(e) => setFiles((current) => [...current, ...Array.from(e.target.files || [])])} />
        </label>
        <div className={`recorder ${recordingTarget === "description" ? "recorder--active" : ""}`}>
          <button
            type="button"
            onClick={recordingTarget === "description" ? stopRecording : () => startRecording("description")}
            disabled={Boolean(recordingTarget && recordingTarget !== "description")}
            aria-label={recordingTarget === "description" ? "Zatrzymaj nagrywanie opisu" : "Rozpocznij nagrywanie opisu prac"}
          >
            <Icon name="mic" size={30} />
          </button>
          <div>
            <strong>{recordingTarget === "description" ? "Nagrywanie opisu..." : transcribing === "description" ? "Zamieniam głos na tekst..." : "Nagraj opis prac"}</strong>
            <span>Powiedz krótko, co zostało zrobione. Tekst pojawi się poniżej i będzie można go poprawić.</span>
          </div>
        </div>
        <label>{kind === "problem" ? "Opis problemu" : "Opis prac"}<textarea rows={5} value={body} onChange={(e) => setBody(e.target.value)} placeholder={kind === "problem" ? "Co się wydarzyło i czego potrzeba?" : "Wpisz opis albo nagraj go przyciskiem powyżej."} /></label>
        <button className="optional-voice-toggle" type="button" onClick={() => setShowVoiceNote((current) => !current)}>
          <Icon name="mic" size={20} />
          <span><strong>Opcjonalna dłuższa notatka głosowa</strong><small>Dodaj szczegóły, ustalenia lub obszerniejszy komentarz.</small></span>
        </button>
        {showVoiceNote && (
          <>
            <div className={`recorder ${recordingTarget === "note" ? "recorder--active" : ""}`}>
              <button
                type="button"
                onClick={recordingTarget === "note" ? stopRecording : () => startRecording("note")}
                disabled={Boolean(recordingTarget && recordingTarget !== "note")}
                aria-label={recordingTarget === "note" ? "Zatrzymaj dłuższą notatkę" : "Rozpocznij dłuższą notatkę głosową"}
              >
                <Icon name="mic" size={30} />
              </button>
              <div>
                <strong>{recordingTarget === "note" ? "Nagrywanie notatki..." : transcribing === "note" ? "Zamieniam notatkę na tekst..." : "Nagraj dłuższą notatkę"}</strong>
                <span>To nagranie również zostanie zapisane i przepisane na tekst.</span>
              </div>
            </div>
            <label>Tekst dłuższej notatki<textarea rows={4} value={voiceNote} onChange={(event) => setVoiceNote(event.target.value)} placeholder="Tutaj pojawi się transkrypcja dłuższej notatki." /></label>
          </>
        )}
        {files.length > 0 && <div className="file-chips">{files.map((file, index) => <span key={`${file.name}-${index}`}>{file.type.startsWith("audio/") ? "Nagranie" : "Zdjęcie"} {index + 1}<button type="button" onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}>×</button></span>)}</div>}
        {error && <p className="form-error">{error}</p>}
        <Button type="submit" busy={busy || Boolean(transcribing)} icon={kind === "problem" ? "alert" : "check"}>{navigator.onLine ? "Zapisz wpis" : "Zapisz do wysłania"}</Button>
      </form>
    </Modal>
  );
}

function TimelineEntry({
  item,
  guestToken,
  onRefresh,
}: {
  item: Entry;
  guestToken?: string;
  onRefresh: () => void;
}) {
  const [comment, setComment] = useState("");
  const [open, setOpen] = useState(false);
  async function addComment(event: FormEvent) {
    event.preventDefault();
    if (!comment.trim()) return;
    await api(`/entries/${item.id}/comments`, { method: "POST", body: JSON.stringify({ body: comment }) }, guestToken);
    setComment("");
    onRefresh();
  }
  async function toggleProblem() {
    await api(`/entries/${item.id}`, {
      method: "PATCH",
      body: JSON.stringify({ problem_status: item.problem_status === "resolved" ? "open" : "resolved" }),
    }, guestToken);
    onRefresh();
  }
  return (
    <article className={`timeline-entry timeline-entry--${item.kind}`}>
      <span className="timeline-entry__marker"><Icon name={item.kind === "problem" ? "alert" : item.media.some((m) => m.kind === "audio") ? "mic" : "camera"} /></span>
      <div className="timeline-entry__body">
        <header>
          <div><strong>{item.kind === "problem" ? "Zgłoszono problem" : item.media.length ? "Dodano dokumentację" : "Dodano aktualizację"}</strong><span>{new Intl.DateTimeFormat("pl", { dateStyle: "medium", timeStyle: "short" }).format(new Date(item.occurred_at))}</span></div>
          <small>{item.author?.name || item.author?.email || item.guest_label || "Gość"}</small>
        </header>
        {item.stage && <span className="stage-label">{item.stage.title}</span>}
        {(item.body || item.transcript) && <p>{item.body || item.transcript}</p>}
        {item.transcript && item.body && <details><summary>Transkrypcja głosu</summary><p>{item.transcript}</p></details>}
        {item.media.some((asset) => asset.kind === "image") && <div className="media-grid">{item.media.filter((asset) => asset.kind === "image").map((asset) => <a href={guestToken ? `${asset.url}?guest_token=${encodeURIComponent(guestToken)}` : asset.url} target="_blank" key={asset.id}><img src={guestToken ? `${asset.url}?guest_token=${encodeURIComponent(guestToken)}` : asset.url} alt={asset.original_name} loading="lazy" /></a>)}</div>}
        {item.media.filter((asset) => asset.kind === "audio").map((asset) => <audio controls src={guestToken ? `${asset.url}?guest_token=${encodeURIComponent(guestToken)}` : asset.url} key={asset.id} />)}
        {item.kind === "problem" && <button className={`problem-toggle problem-toggle--${item.problem_status}`} onClick={toggleProblem}><Icon name="check" size={16} /> {item.problem_status === "resolved" ? "Problem rozwiązany" : "Oznacz jako rozwiązany"}</button>}
        <button className="comment-toggle" onClick={() => setOpen(!open)}>{item.comments.length} komentarzy · {open ? "Ukryj" : "Otwórz"}</button>
        {open && <div className="comments">{item.comments.map((entryComment) => <div key={entryComment.id}><strong>{entryComment.author?.name || entryComment.author?.email || entryComment.guest_label}</strong><p>{entryComment.body}</p></div>)}<form onSubmit={addComment}><input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Dodaj komentarz..." /><Button type="submit" variant="secondary">Wyślij</Button></form></div>}
      </div>
    </article>
  );
}

function ManageProjectModal({
  project,
  user,
  onClose,
  onRefresh,
  notify,
}: {
  project: Project;
  user?: User;
  onClose: () => void;
  onRefresh: () => void;
  notify: (toast: Toast) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [guestUrl, setGuestUrl] = useState("");
  const [invitationUrl, setInvitationUrl] = useState("");
  const [tab, setTab] = useState<"details" | "stages" | "people" | "share">("details");
  const [workers, setWorkers] = useState<WorkerProfile[]>([]);
  const canManagePeople = ["owner", "manager"].includes(project.role || "");
  const canAssignWorkers = canManagePeople && user?.profile_type !== "independent_contractor" && !isCompanyWorker(user);
  const peopleTabLabel = user?.profile_type === "investor" ? "Wykonawcy" : "Majstrowie i ekipy";

  useEffect(() => {
    if (!canAssignWorkers) return;
    api<WorkerProfile[]>("/workers").then(setWorkers).catch(() => setWorkers([]));
  }, [canAssignWorkers]);

  async function saveDetails(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      const payload: Record<string, unknown> = {
        name: data.get("name"),
        client_name: data.get("client_name"),
        client_email: data.get("client_email"),
        address: data.get("address"),
        description: data.get("description"),
        status: data.get("status"),
        portfolio_enabled: data.get("portfolio_enabled") === "on",
        portfolio_slug: data.get("portfolio_slug") || null,
        portfolio_summary: data.get("portfolio_summary"),
      };
      if (canManagePeople) {
        payload.details_locked = data.get("details_locked") === "on";
      }
      if (canAssignWorkers) {
        payload.worker_profile_id = data.get("worker_profile_id") || null;
      }
      await api(`/projects/${project.id}`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      notify({ kind: "success", message: "Ustawienia projektu zapisane" });
      onRefresh();
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zapisać" });
    } finally {
      setBusy(false);
    }
  }

  async function setStageStatus(stageId: string, status: string) {
    await api(`/projects/${project.id}/stages/${stageId}`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    });
    onRefresh();
  }

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const result = await api<{ url: string }>(`/projects/${project.id}/invite`, {
        method: "POST",
        body: JSON.stringify({ email: data.get("email"), role: data.get("role") }),
      });
      setInvitationUrl(result.url);
      const copied = await copyToClipboard(result.url);
      form.reset();
      notify({ kind: copied ? "success" : "info", message: copied ? "Zaproszenie zapisane, a link skopiowany." : "Zaproszenie zapisane. Link jest poniżej do ręcznego skopiowania." });
      onRefresh();
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zaprosić" });
    }
  }

  async function createGuest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const result = await api<{ url: string }>(`/projects/${project.id}/guest-links`, {
        method: "POST",
        body: JSON.stringify({
          label: data.get("label"),
          email: data.get("email"),
          worker_profile_id: data.get("worker_profile_id") || null,
          kind: "worker",
          permission: data.get("permission"),
          expires_in_days: 30,
        }),
      });
      setGuestUrl(result.url);
      const copied = await copyToClipboard(result.url);
      form.reset();
      onRefresh();
      notify({ kind: copied ? "success" : "info", message: copied ? "Link wykonawcy skopiowany. Wyślij go majstrowi przez SMS lub WhatsApp." : "Link wykonawcy jest gotowy poniżej. Skopiuj go ręcznie." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się utworzyć linku" });
    }
  }

  async function assignWorker(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api(`/projects/${project.id}`, {
        method: "PATCH",
        body: JSON.stringify({ worker_profile_id: data.get("worker_profile_id") || null }),
      });
      notify({ kind: "success", message: "Wykonawca przypisany do zlecenia." });
      onRefresh();
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się przypisać wykonawcy" });
    }
  }

  async function rotateGuest(linkId: string) {
    try {
      const result = await api<{ url: string }>(`/projects/${project.id}/guest-links/${linkId}/rotate`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      setGuestUrl(result.url);
      const copied = await copyToClipboard(result.url);
      notify({ kind: copied ? "success" : "info", message: copied ? "Nowy link wykonawcy skopiowany." : "Nowy link wykonawcy jest poniżej. Skopiuj go ręcznie." });
      onRefresh();
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się odświeżyć linku" });
    }
  }

  return (
    <Modal title="Edytuj wybrane zlecenie" onClose={onClose} wide>
      <div className="manage-tabs">
        <button className={tab === "details" ? "active" : ""} onClick={() => setTab("details")}>Dane</button>
        <button className={tab === "stages" ? "active" : ""} onClick={() => setTab("stages")}>Etapy</button>
        {canAssignWorkers && <button className={tab === "people" ? "active" : ""} onClick={() => setTab("people")}>{peopleTabLabel}</button>}
        {canAssignWorkers && <button className={tab === "share" ? "active" : ""} onClick={() => setTab("share")}>Link dla majstra tymczasowego</button>}
      </div>
      {tab === "details" && (
        <form className="form-stack" onSubmit={saveDetails}>
          <div className="form-row">
            <label>Nazwa<input name="name" defaultValue={project.name} required /></label>
            <label>Status<select name="status" defaultValue={project.status}><option value="assigned">Zlecone</option><option value="in_progress">W realizacji</option><option value="completed">Zakończono</option></select></label>
          </div>
          <div className="form-row">
            <label>Klient<input name="client_name" defaultValue={project.client_name} /></label>
            <label>E-mail klienta<input name="client_email" type="email" defaultValue={project.client_email} /></label>
          </div>
          <label>Adres<input name="address" defaultValue={project.address} /></label>
          {canAssignWorkers && (
            <label>
              {user?.profile_type === "investor" ? "Wykonawca" : "Majster / ekipa"}
              <select name="worker_profile_id" defaultValue={project.worker_profile_id || ""}>
                <option value="">Bez przypisanego wykonawcy</option>
                {workers.map((worker) => (
                  <option value={worker.id} key={worker.id}>
                    {workerKindLabel(worker)}: {worker.label} - {workerAccountLabel(worker)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label>Opis<textarea name="description" rows={3} defaultValue={project.description} /></label>
          <div className="portfolio-settings">
            <label className="check-label"><input type="checkbox" name="portfolio_enabled" defaultChecked={project.portfolio_enabled} /> Pokaż realizację w publicznym portfolio</label>
            <label>Adres portfolio<input name="portfolio_slug" defaultValue={project.portfolio_slug || ""} placeholder="np. firma-kowalski" /></label>
            <label>Opis realizacji<textarea name="portfolio_summary" rows={3} defaultValue={project.portfolio_summary} /></label>
          </div>
          {canManagePeople && (
            <label className="check-label">
              <input type="checkbox" name="details_locked" defaultChecked={project.details_locked} />
              Zablokuj majstrom edycję danych zlecenia. Nadal mogą dodawać zdjęcia, opisy i problemy.
            </label>
          )}
          <Button type="submit" busy={busy}>Zapisz dane</Button>
        </form>
      )}
      {tab === "stages" && (
        <div className="manage-content">
          <div className="manage-stage-list">
            {project.stages?.map((stage) => (
              <article key={stage.id}>
                <span>{stage.position + 1}</span>
                <strong>{stage.title}</strong>
                <select value={stage.status} onChange={(e) => setStageStatus(stage.id, e.target.value)}>
                  <option value="planned">Zaplanowany</option>
                  <option value="active">W trakcie</option>
                  <option value="completed">Zakończony</option>
                </select>
              </article>
            ))}
          </div>
          <p className="form-intro">Etapy są celowo ograniczone do trzech prostych momentów: przed pracą, w trakcie i po zakończeniu.</p>
        </div>
      )}
      {tab === "people" && canAssignWorkers && (
        <div className="manage-content">
          <div className="guest-explainer">
            <Icon name="users" size={34} />
            <div>
              <h3>{peopleTabLabel}</h3>
              <p>Wybierz stałego wykonawcę przypisanego do tego zlecenia albo wyślij osobne zaproszenie e-mail do konta.</p>
            </div>
          </div>
          <form className="inline-form inline-form--three" onSubmit={assignWorker}>
            <select name="worker_profile_id" defaultValue={project.worker_profile_id || ""}>
              <option value="">Bez przypisanego wykonawcy</option>
              {workers.map((worker) => (
                <option value={worker.id} key={worker.id}>
                    {workerKindLabel(worker)}: {worker.label} - {workerAccountLabel(worker)}
                </option>
              ))}
            </select>
            <Button type="submit">Przypisz wykonawcę</Button>
          </form>
          {project.worker_profile && (
            <div className="member-list worker-link-list">
              <h4>Aktualnie przypisany wykonawca</h4>
              <article>
                <span>{project.worker_profile.label.slice(0, 2).toUpperCase()}</span>
                <div>
                  <strong>{project.worker_profile.label}</strong>
                  <small>{project.worker_profile.email || "Bez e-maila"} · {project.worker_profile.account_type === "account" ? "konto po potwierdzeniu e-mail" : "link-only"}</small>
                </div>
                <b>Przypisany</b>
              </article>
            </div>
          )}
          <div className="member-list">{project.members?.map((member) => <article key={member.id}><span>{(member.user.name || member.user.email).slice(0, 2).toUpperCase()}</span><div><strong>{member.user.name || member.user.email}</strong><small>{member.user.email}</small></div><b>{member.role}</b></article>)}</div>
          <form className="inline-form inline-form--three" onSubmit={invite}><input type="email" name="email" placeholder="E-mail współpracownika" required /><select name="role" defaultValue="contributor"><option value="viewer">Podgląd</option><option value="contributor">Dodawanie wpisów</option><option value="manager">Zarządzanie</option></select><Button type="submit">Zaproś</Button></form>
          {invitationUrl && <div className="share-result"><input value={invitationUrl} readOnly /><Button variant="secondary" onClick={() => void copyToClipboard(invitationUrl)}>Kopiuj link</Button></div>}
        </div>
      )}
      {tab === "share" && canAssignWorkers && (
        <div className="manage-content">
          <div className="guest-explainer"><Icon name="link" size={34} /><div><h3>Link dla majstra lub ekipy bez logowania</h3><p>Wpisz nazwę wykonawcy. E-mail jest opcjonalny. Link otwiera tylko to zlecenie i pozwala dodać postęp zgodnie z uprawnieniami.</p></div></div>
          <form className="form-stack form-stack--flat" onSubmit={createGuest}>
            {workers.length > 0 && (
              <label>
                Wybierz wykonawcę z listy
                <select name="worker_profile_id" defaultValue={project.worker_profile_id || ""}>
                  <option value="">Nie przypinaj do profilu</option>
                  {workers.map((worker) => (
                    <option value={worker.id} key={worker.id}>
                      {workerKindLabel(worker)}: {worker.label} - {workerAccountLabel(worker)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label>Majster / ekipa<input name="label" placeholder="np. Mieciu, ekipa łazienka" required /></label>
            <label>E-mail opcjonalnie<input type="email" name="email" placeholder="Możesz zostawić puste" /></label>
            <label>Uprawnienia<select name="permission" defaultValue="history"><option value="add">Tylko dodawanie</option><option value="history">Dodawanie i historia</option><option value="view">Tylko podgląd</option></select></label>
            <Button type="submit" icon="link">Utwórz i skopiuj link</Button>
          </form>
          {guestUrl && <div className="share-result"><input value={guestUrl} readOnly /><Button variant="secondary" onClick={() => void copyToClipboard(guestUrl)}>Kopiuj</Button></div>}
          {(project.worker_links?.length || 0) > 0 && (
            <div className="member-list worker-link-list">
              <h4>Linki wykonawców do tego zlecenia</h4>
              {project.worker_links?.map((link) => (
                <article key={link.id}>
                  <span>{link.label.slice(0, 2).toUpperCase()}</span>
                  <div>
                    <strong>{link.label}</strong>
                    <small>{link.email || "Bez e-maila"} · {link.account_type === "account" ? "konto stałe / email" : "link-only"}</small>
                  </div>
                  <b>{link.permission === "history" ? "Dodaje + historia" : link.permission === "add" ? "Dodaje" : "Podgląd"}</b>
                  {!link.revoked_at && <Button type="button" variant="secondary" onClick={() => rotateGuest(link.id)}>Odśwież link</Button>}
                </article>
              ))}
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function ReportModal({
  project,
  reports,
  onClose,
  onRefresh,
  notify,
}: {
  project: Project;
  reports: Report[];
  onClose: () => void;
  onRefresh: () => void;
  notify: (toast: Toast) => void;
}) {
  const [selected, setSelected] = useState<Report | null>(reports[0] || null);
  const [busy, setBusy] = useState(false);
  const [share, setShare] = useState<{ report: Report; url: string; qr_url: string; pdf_url: string } | null>(null);
  const [draftContent, setDraftContent] = useState<Report["content"] | null>(reports[0]?.content || null);

  useEffect(() => {
    if (!selected) return;
    const current = reports.find((item) => item.id === selected.id);
    if (current) {
      setSelected(current);
      setDraftContent(current.content);
    }
  }, [reports]);

  async function create() {
    setBusy(true);
    try {
      const report = await api<Report>(`/projects/${project.id}/reports`, {
        method: "POST",
        body: JSON.stringify({
          title: `Raport — ${new Intl.DateTimeFormat("pl").format(new Date())}`,
          report_type: project.status === "completed" ? "final" : "periodic",
        }),
      });
      setSelected(report);
      setDraftContent(report.content);
      notify({ kind: "info", message: "Raport jest generowany. To potrwa chwilę." });
      onRefresh();
    } finally {
      setBusy(false);
    }
  }

  async function saveDraft() {
    if (!selected || !draftContent) return;
    setBusy(true);
    try {
      const updated = await api<Report>(`/reports/${selected.id}`, {
        method: "PATCH",
        body: JSON.stringify({ content: draftContent }),
      });
      setSelected(updated);
      notify({ kind: "success", message: "Poprawki w raporcie zapisane" });
      onRefresh();
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zapisać raportu" });
    } finally {
      setBusy(false);
    }
  }

  async function publish() {
    if (!selected) return;
    setBusy(true);
    try {
      const result = await api<{ report: Report; url: string; qr_url: string; pdf_url: string }>(`/reports/${selected.id}/publish`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      setShare(result);
      setSelected(result.report);
      setDraftContent(result.report.content);
      const copied = await copyToClipboard(result.url);
      notify({ kind: copied ? "success" : "info", message: copied ? "Raport opublikowany. Link skopiowano." : "Raport opublikowany. Link jest poniżej do ręcznego skopiowania." });
      onRefresh();
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się opublikować raportu" });
    } finally {
      setBusy(false);
    }
  }

  async function removeReport() {
    if (!selected || !window.confirm(`Usunąć raport „${selected.title}”?`)) return;
    setBusy(true);
    try {
      await api(`/reports/${selected.id}`, { method: "DELETE" });
      setSelected(null);
      setDraftContent(null);
      setShare(null);
      notify({ kind: "success", message: "Raport został usunięty." });
      onRefresh();
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się usunąć raportu" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Raporty PDF dla klienta" onClose={onClose} wide>
      <div className="report-builder">
        <aside>
          <Button icon="plus" onClick={create} busy={busy}>Nowy raport</Button>
          <div className="report-list">{reports.map((report) => <button className={selected?.id === report.id ? "active" : ""} onClick={() => { setSelected(report); setDraftContent(report.content); }} key={report.id}><strong>{report.title}</strong><span>{report.status === "generating" ? "Generowanie..." : report.status === "published" ? "Opublikowany" : "Szkic"}</span></button>)}</div>
        </aside>
        <div className="report-preview">
          {!selected ? <EmptyState icon="report" title="Utwórz pierwszy raport" text="System zbierze wpisy i ułoży je według etapów projektu." /> : selected.status === "generating" ? <EmptyState icon="sync" title="Układamy raport" text="Zdjęcia i opisy są porządkowane. Odśwież widok za chwilę."><Button variant="secondary" onClick={onRefresh}>Odśwież</Button></EmptyState> : <>
            <div className="report-paper">
              <header><img src="/brand/logo.png" alt="Pan Majster" /><span>{new Intl.DateTimeFormat("pl").format(new Date(selected.created_at))}</span></header>
              <h2>{selected.title}</h2>
              <h3>{project.name}</h3>
              {selected.status === "published" ? <p className="report-summary">{selected.content?.summary}</p> : <textarea className="report-edit-summary" value={draftContent?.summary || ""} onChange={(e) => setDraftContent((current) => ({ ...(current || {}), summary: e.target.value }))} />}
              {(draftContent?.stages || selected.content?.stages)?.map((stage, stageIndex) => <section key={`${stage.title}-${stageIndex}`}><h4>{stage.title}</h4>{stage.entries.map((entry, entryIndex) => <div className="report-entry" key={entry.entry_id}><span>{entry.date}</span>{selected.status === "published" ? <p>{entry.text}</p> : <textarea value={entry.text} onChange={(e) => setDraftContent((current) => { const copy = structuredClone(current || {}); if (copy.stages) copy.stages[stageIndex].entries[entryIndex].text = e.target.value; return copy; })} />}</div>)}</section>)}
            </div>
            <div className="report-actions">
              {selected.status !== "published" && <Button variant="secondary" onClick={saveDraft} busy={busy}>Zapisz poprawki</Button>}
              {selected.status !== "published" && <Button icon="send" onClick={publish} busy={busy}>Zatwierdź i opublikuj</Button>}
              {selected.pdf_url && <a className="button button--secondary" href={selected.pdf_url} target="_blank">Otwórz PDF</a>}
              <Button variant="danger" onClick={removeReport} busy={busy}>Usuń raport</Button>
              {share && <><input readOnly value={share.url} /><Button variant="secondary" icon="link" onClick={() => void copyToClipboard(share.url)}>Kopiuj link</Button><img className="qr-preview" src={share.qr_url} alt="Kod QR raportu" /></>}
            </div>
          </>}
        </div>
      </div>
    </Modal>
  );
}

function ProjectView({
  projectId,
  guestToken,
  user,
  onUserUpdated,
  onBack,
  notify,
  onQueue,
}: {
  projectId: string;
  guestToken?: string;
  user?: User;
  onUserUpdated?: (user: User) => void;
  onBack: () => void;
  notify: (toast: Toast) => void;
  onQueue: () => void;
}) {
  const [project, setProject] = useState<Project | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [clientLink, setClientLink] = useState<ClientLink | null>(null);
  const [loading, setLoading] = useState(true);
  const [entryModal, setEntryModal] = useState<{ kind: "update" | "problem"; mode: "photo" | "audio" | "text" } | null>(null);
  const [showReports, setShowReports] = useState(false);
  const [showManage, setShowManage] = useState(false);
  const [showClientLink, setShowClientLink] = useState(false);
  const [fieldMode, setFieldMode] = useState(
    Boolean(guestToken) || user?.preferred_mode === "field",
  );

  const load = useCallback(async () => {
    try {
      const [projectData, entryData] = await Promise.all([
        api<Project>(`/projects/${projectId}`, {}, guestToken),
        api<Entry[]>(`/projects/${projectId}/entries`, {}, guestToken),
      ]);
      setProject(projectData);
      setEntries(entryData);
      if (!guestToken) {
        const [reportData, linkData] = await Promise.all([
          api<Report[]>(`/projects/${projectId}/reports`),
          api<ClientLink>(`/projects/${projectId}/client-link`),
        ]);
        setReports(reportData);
        setClientLink(linkData);
      }
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się otworzyć projektu" });
    } finally {
      setLoading(false);
    }
  }, [guestToken, notify, projectId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!reports.some((report) => report.status === "generating")) return;
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [load, reports]);

  if (loading) return <div className="page"><div className="loading-screen"><span className="spinner" /> Ładowanie projektu...</div></div>;
  if (!project) return <div className="page"><EmptyState icon="alert" title="Nie udało się otworzyć projektu" text="Link może być nieaktywny albo nie masz dostępu." /></div>;

  const canAdd = !project.guest || ["add", "history"].includes(project.guest.permission);
  const completed = project.stages?.filter((stage) => stage.status === "completed").length || 0;
  const progress = project.stages?.length ? Math.round((completed / project.stages.length) * 100) : 0;
  const canUseReports = !guestToken && !isCompanyWorker(user);

  async function changeMode(next: "field" | "expanded") {
    setFieldMode(next === "field");
    if (!user || !onUserUpdated) return;
    try {
      const updated = await api<User>("/me", {
        method: "PATCH",
        body: JSON.stringify({ preferred_mode: next }),
      });
      onUserUpdated(updated);
    } catch {
      notify({ kind: "info", message: "Tryb zmieniono, ale nie udało się zapisać ustawienia." });
    }
  }

  async function copyClientLink() {
    if (!clientLink?.url) return;
    setShowClientLink(true);
    const copied = await copyToClipboard(clientLink.url);
    notify({ kind: copied ? "success" : "info", message: copied ? "Stały link klienta został skopiowany." : "Stały link klienta jest poniżej. Skopiuj go ręcznie." });
  }

  const stages = (
    <div className="simple-stages">
      {project.stages?.map((stage, index) => (
        <div className={`simple-stage simple-stage--${stage.status}`} key={stage.id}>
          <span>{stage.status === "completed" ? "✓" : index + 1}</span>
          <div>
            <strong>{stage.title}</strong>
            <small>
              {stage.status === "completed"
                ? "Zakończony"
                : stage.status === "active"
                  ? "Aktualny etap"
                  : "Do wykonania"}
            </small>
          </div>
        </div>
      ))}
    </div>
  );

  if (fieldMode) {
    return (
      <div className="field-mode">
        <header className="field-mode__header">
          <button onClick={onBack}><Icon name="back" /></button>
          <div className="field-brand"><img src="/brand/app-icon.png" alt="" /><strong>Pan Majster</strong></div>
          {!guestToken ? (
            <button onClick={() => changeMode("expanded")} aria-label="Przejdź do trybu rozbudowanego">
              <Icon name="menu" />
            </button>
          ) : <span />}
        </header>
        <main>
          <section className="field-project">
            <small>ZLECENIE</small>
            <h1>{project.name}</h1>
            <p>{project.address}</p>
            <span className={`status status--${project.status}`}>● {statusLabels[project.status]}</span>
            {!guestToken && (
              <button className="field-mode-label" onClick={() => changeMode("expanded")}>
                Tryb terenowy / prosty · przejdź do rozbudowanego
              </button>
            )}
          </section>
          {!navigator.onLine && <div className="offline-banner"><Icon name="sync" /> Tryb offline — wpis zostanie wysłany po odzyskaniu sieci.</div>}
          {canAdd && <div className="field-actions">
            <FieldAction icon="camera" title="Dodaj postęp" subtitle="Zdjęcia + opis głosowy lub tekst" tone="navy" onClick={() => setEntryModal({ kind: "update", mode: "photo" })} />
            <FieldAction icon="alert" title="Zgłoś problem" subtitle="Usterka lub decyzja" tone="red" onClick={() => setEntryModal({ kind: "problem", mode: "photo" })} />
            {!guestToken && user?.profile_type !== "investor" && !isCompanyWorker(user) && <FieldAction icon="link" title="Link klienta" subtitle="Kopiuj jeden stały link" tone="orange" onClick={copyClientLink} />}
            {canUseReports && <FieldAction icon="report" title="Raporty PDF" subtitle="Obejrzyj lub utwórz raport" tone="navy" onClick={() => setShowReports(true)} />}
          </div>}
          {showClientLink && clientLink?.url && (
            <div className="share-result">
              <input value={clientLink.url} readOnly />
              <Button variant="secondary" onClick={() => void copyToClipboard(clientLink.url)}>Kopiuj link</Button>
            </div>
          )}
          <section className="field-stages">
            <div className="section-title"><h2>Etapy zlecenia</h2></div>
            {stages}
          </section>
          <section className="field-latest">
            <div className="section-title"><h2>Postęp i historia zlecenia</h2></div>
            {entries.length === 0 ? <EmptyState icon="camera" title="Jeszcze bez wpisów" text="Dodaj pierwszy postęp prac: zdjęcia i krótki opis." /> : entries.map((entry) => <TimelineEntry item={entry} guestToken={guestToken} onRefresh={load} key={entry.id} />)}
          </section>
        </main>
        {entryModal && <NewEntryModal project={project} kind={entryModal.kind} mode={entryModal.mode} guestToken={guestToken} onClose={() => setEntryModal(null)} onSaved={() => { setEntryModal(null); load(); notify({ kind: "success", message: "Wpis zapisany" }); }} onQueued={() => { setEntryModal(null); onQueue(); notify({ kind: "info", message: "Wpis zapisany offline i czeka na wysłanie" }); }} />}
        {showReports && <ReportModal project={project} reports={reports} onClose={() => setShowReports(false)} onRefresh={load} notify={notify} />}
      </div>
    );
  }

  return (
    <div className="page project-page">
      <header className="project-header">
        <button className="back-button" onClick={onBack}><Icon name="back" /> Wróć do zleceń</button>
        <div className="project-header__main">
          <div><span className={`status status--${project.status}`}>{statusLabels[project.status]}</span><h1>{project.name}</h1><p>{project.client_name} · {project.address}</p></div>
          <div className="project-header__actions">
            <Button variant="secondary" onClick={() => changeMode("field")}>Tryb terenowy / prosty</Button>
            {!guestToken && user?.profile_type !== "investor" && !isCompanyWorker(user) && clientLink && <Button variant="secondary" icon="link" onClick={copyClientLink}>Link klienta</Button>}
            {!guestToken && project.can_edit_details && <Button variant="secondary" icon="settings" onClick={() => setShowManage(true)}>Edytuj zlecenie</Button>}
            {canUseReports && <Button icon="report" onClick={() => setShowReports(true)}>Raporty PDF</Button>}
          </div>
        </div>
        {showClientLink && clientLink?.url && (
          <div className="share-result client-link-result">
            <input value={clientLink.url} readOnly />
            <Button variant="secondary" onClick={() => void copyToClipboard(clientLink.url)}>Kopiuj link</Button>
          </div>
        )}
      </header>
      <div className="project-layout">
        <aside className="project-summary panel">
          <h3>Podsumowanie zlecenia</h3>
          <div className="progress-value"><strong>{progress}%</strong><span>{completed} z {project.stages?.length || 0} etapów</span></div>
          <div className="progress"><i style={{ width: `${progress}%` }} /></div>
          {stages}
          {!guestToken && <div className="summary-meta"><div><small>Klient</small><strong>{project.client_name || "—"}</strong></div><div><small>Adres</small><strong>{project.address || "—"}</strong></div><div><small>Rola</small><strong>{project.role || "gość"}</strong></div></div>}
        </aside>
        <main className="project-timeline panel">
          <div className="panel__header">
            <div><h2>Postęp i zarządzanie zleceniem</h2><p>Zdjęcia, opisy, ustalenia i problemy w jednej osi czasu.</p></div>
            {canAdd && <div className="quick-buttons"><button onClick={() => setEntryModal({ kind: "update", mode: "photo" })}><Icon name="camera" /> Dodaj postęp</button><button className="problem" onClick={() => setEntryModal({ kind: "problem", mode: "photo" })}><Icon name="alert" /> Zgłoś problem</button></div>}
          </div>
          {entries.length === 0 ? <EmptyState icon="camera" title="Tu powstanie historia pracy" text="Dodaj pierwszy postęp: zdjęcia oraz opis głosowy lub tekstowy." /> : <div className="timeline">{entries.map((entry) => <TimelineEntry item={entry} guestToken={guestToken} onRefresh={load} key={entry.id} />)}</div>}
        </main>
      </div>
      {entryModal && <NewEntryModal project={project} kind={entryModal.kind} mode={entryModal.mode} guestToken={guestToken} onClose={() => setEntryModal(null)} onSaved={() => { setEntryModal(null); load(); notify({ kind: "success", message: "Wpis zapisany" }); }} onQueued={() => { setEntryModal(null); onQueue(); notify({ kind: "info", message: "Wpis zapisany offline" }); }} />}
      {showReports && <ReportModal project={project} reports={reports} onClose={() => setShowReports(false)} onRefresh={load} notify={notify} />}
      {showManage && <ManageProjectModal project={project} user={user} onClose={() => setShowManage(false)} onRefresh={load} notify={notify} />}
    </div>
  );
}

function ReportsPage({ projects, onOpen }: { projects: Project[]; onOpen: (project: Project) => void }) {
  return (
    <div className="page">
      <header className="page-header"><div><span className="eyebrow">Dokumentacja</span><h1>Raporty</h1><p>Otwórz projekt, aby przygotować raport okresowy lub końcowy.</p></div></header>
      <section className="panel">
        <div className="project-cards">{projects.map((project) => <button className="project-card" key={project.id} onClick={() => onOpen(project)}><div className="project-card__top"><span className="project-card__icon"><Icon name="report" /></span><span className={`status status--${project.status}`}>{statusLabels[project.status]}</span></div><h3>{project.name}</h3><p>{project.client_name || "Bez klienta"}</p><div className="project-card__footer"><span>Otwórz raporty</span><Icon name="back" className="chevron" /></div></button>)}</div>
      </section>
    </div>
  );
}

function WorkspaceModal({
  workspaceId,
  onClose,
  onChanged,
  notify,
}: {
  workspaceId: string;
  onClose: () => void;
  onChanged: () => void;
  notify: (toast: Toast) => void;
}) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [invitationUrl, setInvitationUrl] = useState("");
  const [editingWorker, setEditingWorker] = useState<WorkerProfile | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<Workspace>(`/workspaces/${workspaceId}`).then(setWorkspace).catch((reason) => {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się otworzyć firmy" });
    });
  }, [notify, workspaceId]);

  useEffect(() => { load(); }, [load]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      const updated = await api<Workspace>(`/workspaces/${workspaceId}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: data.get("name"),
          description: data.get("description"),
          phone: data.get("phone"),
          address: data.get("address"),
        }),
      });
      setWorkspace(updated);
      onChanged();
      notify({ kind: "success", message: "Dane firmy zostały zapisane." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zapisać firmy" });
    } finally {
      setBusy(false);
    }
  }

  async function createWorker(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const worker = await api<WorkerProfile & { invitation_url?: string; message?: string; existing?: boolean }>("/workers", {
        method: "POST",
        body: JSON.stringify({
          label: formString(data, "label"),
          profile_kind: formString(data, "profile_kind") || "craftsman",
          email: formString(data, "email"),
          phone: formString(data, "phone"),
          note: formString(data, "note"),
          workspace_id: workspaceId,
        }),
      });
      setInvitationUrl(worker.invitation_url || "");
      form.reset();
      load();
      onChanged();
      notify({ kind: worker.existing ? "info" : "success", message: worker.message || "Wykonawca dodany. Możesz przypisać go do zlecenia." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się dodać wykonawcy" });
    } finally {
      setBusy(false);
    }
  }

  async function saveWorker(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingWorker) return;
    setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      const updated = await api<WorkerProfile>(`/workers/${editingWorker.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          label: formString(data, "label"),
          profile_kind: formString(data, "profile_kind") || editingWorker.profile_kind,
          email: formString(data, "email"),
          phone: formString(data, "phone"),
          note: formString(data, "note"),
        }),
      });
      setEditingWorker(null);
      setWorkspace((current) => current ? {
        ...current,
        worker_profiles: current.worker_profiles?.map((worker) => worker.id === updated.id ? updated : worker),
      } : current);
      load();
      onChanged();
      notify({ kind: "success", message: "Dane wykonawcy zapisane." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zapisać wykonawcy" });
    } finally {
      setBusy(false);
    }
  }

  async function deactivateWorker(worker: WorkerProfile) {
    const warning = worker.assigned_projects.length > 0
      ? "Ten majster/ekipa ma przypisane zlecenia. Dezaktywacja odepnie wykonawcę od tych zleceń. Kontynuować?"
      : "Dezaktywować tego majstra/ekipę?";
    if (!window.confirm(warning)) return;
    setBusy(true);
    try {
      await api(`/workers/${worker.id}`, { method: "DELETE" });
      load();
      onChanged();
      notify({ kind: "success", message: "Majster/ekipa została dezaktywowana." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się dezaktywować majstra/ekipy" });
    } finally {
      setBusy(false);
    }
  }

  async function activateWorker(worker: WorkerProfile) {
    setBusy(true);
    try {
      await api(`/workers/${worker.id}/activate`, { method: "POST" });
      load();
      onChanged();
      notify({ kind: "success", message: "Majster/ekipa została aktywowana ponownie." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się aktywować majstra/ekipy" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={workspace?.kind === "personal" ? "Wykonawcy" : "Firma i majstrowie"} onClose={onClose} wide>
      {!workspace ? <div className="loading-screen"><span className="spinner" /> Ładowanie firmy...</div> : (
        <div className="workspace-editor">
          <form className="form-stack" onSubmit={save}>
            <h3>{workspace.kind === "personal" ? "Dane listy wykonawców" : "Dane firmy"}</h3>
            <label>{workspace.kind === "personal" ? "Nazwa listy wykonawców" : "Nazwa firmy"}<input name="name" defaultValue={workspace.name} required /></label>
            <label>Opis<textarea name="description" rows={3} defaultValue={workspace.description} placeholder="Czym zajmuje się firma?" /></label>
            <div className="form-row">
              <label>Telefon<input name="phone" defaultValue={workspace.phone} /></label>
              <label>Adres<input name="address" defaultValue={workspace.address} /></label>
            </div>
            <Button type="submit" busy={busy}>Zapisz dane {workspace.kind === "personal" ? "listy" : "firmy"}</Button>
          </form>
          <section className="workspace-members">
            <h3>{workspace.kind === "personal" ? "Wykonawcy" : "Majstrowie i ekipy"}</h3>
            <div className="member-list">
              {workspace.members?.map((member) => (
                <article key={member.id}>
                  <span>{(member.user.name || member.user.email).slice(0, 2).toUpperCase()}</span>
                  <div><strong>{member.user.name || member.user.email}</strong><small>{member.user.email}</small></div>
                  <b>{member.role === "owner" ? "Szef firmy" : member.role === "admin" ? "Administrator" : "Majster"}</b>
                </article>
              ))}
            </div>
            {(workspace.worker_profiles?.length || 0) > 0 && (
              <div className="member-list worker-link-list">
                <h4>Wykonawcy / majstrowie / ekipy</h4>
                {workspace.worker_profiles?.map((worker) => (
                  <article className={`clickable-card ${!worker.active ? "is-muted" : ""}`} key={worker.id}>
                    <span>{worker.label.slice(0, 2).toUpperCase()}</span>
                    <div>
                      <strong>{workerKindLabel(worker)}: {worker.label}</strong>
                      <small>
                        {worker.email || "Bez e-maila"} · {worker.account_type === "account" ? "konto po potwierdzeniu e-mail" : "link-only"} · {worker.assigned_projects.length} zleceń
                      </small>
                    </div>
                    <Button type="button" variant="secondary" onClick={() => setEditingWorker(worker)}>Edytuj</Button>
                    {worker.active ? (
                      <Button type="button" variant="danger" disabled={busy} onClick={() => deactivateWorker(worker)}>Dezaktywuj</Button>
                    ) : (
                      <Button type="button" variant="secondary" disabled={busy} onClick={() => activateWorker(worker)}>Aktywuj ponownie</Button>
                    )}
                  </article>
                ))}
              </div>
            )}
            {(workspace.worker_links?.length || 0) > 0 && (
              <div className="member-list worker-link-list">
                <h4>Wykonawcy przypisani linkiem</h4>
                {workspace.worker_links?.map((link) => (
                  <article key={link.id}>
                    <span>{link.label.slice(0, 2).toUpperCase()}</span>
                    <div>
                      <strong>{link.label}</strong>
                      <small>{link.email || "Bez e-maila"} · {link.project_name || "Zlecenie"} · {link.account_type === "account" ? "konto/email" : "link-only"}</small>
                    </div>
                    <b>{link.revoked_at ? "Odwołany" : "Aktywny"}</b>
                  </article>
                ))}
              </div>
            )}
            <form className="form-stack form-stack--flat" onSubmit={createWorker}>
              <h4>{workspace.kind === "personal" ? "Dodaj wykonawcę" : "Dodaj majstra / ekipę"}</h4>
              <p className="form-intro">
                E-mail jest opcjonalny. Jeśli go podasz, utworzymy zaproszenie do stałego konta wykonawcy.
                Wykonawca musi potwierdzić adres kodem z poczty. Bez e-maila możesz użyć linku do konkretnego zlecenia.
              </p>
              <label>Typ<select name="profile_kind" defaultValue="craftsman"><option value="craftsman">{workspace.kind === "personal" ? "Wykonawca / majster" : "Majster"}</option><option value="crew">Ekipa</option></select></label>
              <label>Nazwa majstra / ekipy<input name="label" required placeholder="np. Mieciu hydraulik" /></label>
              <div className="form-row">
                <label>E-mail opcjonalnie<input type="email" name="email" placeholder="Możesz zostawić puste" /></label>
                <label>Telefon opcjonalnie<input name="phone" /></label>
              </div>
              <label>Notatka<textarea name="note" rows={2} placeholder="np. robi łazienki i instalacje" /></label>
              <Button type="submit" busy={busy} icon="plus">{workspace.kind === "personal" ? "Dodaj wykonawcę" : "Dodaj majstra / ekipę"}</Button>
            </form>
            {invitationUrl && <div className="share-result"><input value={invitationUrl} readOnly /><Button variant="secondary" onClick={() => void copyToClipboard(invitationUrl)}>Kopiuj link</Button></div>}
          </section>
        </div>
      )}
      {editingWorker && (
        <Modal title="Edytuj wykonawcę" onClose={() => setEditingWorker(null)}>
          <form className="form-stack" onSubmit={saveWorker}>
            <p className="form-intro">
              {editingWorker.email
                ? "Konto stałe po potwierdzeniu e-mail kodem."
                : "Link-only / tymczasowy wykonawca. E-mail możesz dodać później."}
            </p>
            <label>Typ<select name="profile_kind" defaultValue={editingWorker.profile_kind}><option value="craftsman">Majster / pojedynczy wykonawca</option><option value="crew">Ekipa</option></select></label>
            <label>Nazwa<input name="label" defaultValue={editingWorker.label} required autoFocus /></label>
            <label>E-mail opcjonalnie<input type="email" name="email" defaultValue={editingWorker.email} placeholder="Możesz zostawić puste" /></label>
            <label>Telefon<input name="phone" defaultValue={editingWorker.phone} /></label>
            <label>Notatka<textarea name="note" rows={3} defaultValue={editingWorker.note} /></label>
            <Button type="submit" busy={busy}>Zapisz wykonawcę</Button>
          </form>
        </Modal>
      )}
    </Modal>
  );
}

function TeamWorkerCard({
  worker,
  busy,
  onEdit,
  onDeactivate,
  onActivate,
}: {
  worker: WorkerProfile;
  busy: boolean;
  onEdit: (worker: WorkerProfile) => void;
  onDeactivate: (worker: WorkerProfile) => void;
  onActivate: (worker: WorkerProfile) => void;
}) {
  return (
    <article className={`team-worker-card ${!worker.active ? "is-muted" : ""}`}>
      <div className="team-worker-card__avatar">{worker.label.slice(0, 2).toUpperCase()}</div>
      <div className="team-worker-card__main">
        <div className="team-worker-card__top">
          <strong>{worker.label}</strong>
          <span>{workerKindLabel(worker)}</span>
        </div>
        <small>
          {workerAccountLabel(worker)} · {worker.email || "bez e-maila"}{worker.phone ? ` · ${worker.phone}` : ""}
        </small>
        <div className="assigned-projects">
          <b>Przypisane zlecenia:</b>
          {worker.assigned_projects.length === 0 ? (
            <span>brak</span>
          ) : (
            worker.assigned_projects.map((project) => (
              <span key={project.id}>{project.name} ({statusLabels[project.status] || project.status})</span>
            ))
          )}
        </div>
      </div>
      <div className="team-worker-card__actions">
        <Button type="button" variant="secondary" onClick={() => onEdit(worker)}>Edytuj</Button>
        {worker.active ? (
          <Button type="button" variant="danger" disabled={busy} onClick={() => onDeactivate(worker)}>Dezaktywuj</Button>
        ) : (
          <Button type="button" variant="secondary" disabled={busy} onClick={() => onActivate(worker)}>Aktywuj ponownie</Button>
        )}
      </div>
    </article>
  );
}

function CompanyTeamPanel({
  workspaceId,
  onChanged,
  notify,
}: {
  workspaceId: string;
  onChanged: () => void;
  notify: (toast: Toast) => void;
}) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [invitationUrl, setInvitationUrl] = useState("");
  const [editingWorker, setEditingWorker] = useState<WorkerProfile | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<Workspace>(`/workspaces/${workspaceId}`).then(setWorkspace).catch((reason) => {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się otworzyć firmy" });
    });
  }, [notify, workspaceId]);

  useEffect(() => { load(); }, [load]);

  async function saveCompany(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      const updated = await api<Workspace>(`/workspaces/${workspaceId}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: data.get("name"),
          description: data.get("description"),
          phone: data.get("phone"),
          address: data.get("address"),
        }),
      });
      setWorkspace(updated);
      onChanged();
      notify({ kind: "success", message: "Dane firmy zostały zapisane." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zapisać firmy" });
    } finally {
      setBusy(false);
    }
  }

  async function createWorker(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      const worker = await api<WorkerProfile & { invitation_url?: string; message?: string; existing?: boolean }>("/workers", {
        method: "POST",
        body: JSON.stringify({
          label: formString(data, "label"),
          profile_kind: formString(data, "profile_kind") || "craftsman",
          email: formString(data, "email"),
          phone: formString(data, "phone"),
          note: formString(data, "note"),
          workspace_id: workspaceId,
        }),
      });
      setInvitationUrl(worker.invitation_url || "");
      form.reset();
      load();
      onChanged();
      notify({ kind: worker.existing ? "info" : "success", message: worker.message || "Majster/ekipa dodana." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się dodać majstra/ekipy" });
    } finally {
      setBusy(false);
    }
  }

  async function saveWorker(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingWorker) return;
    setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      const updated = await api<WorkerProfile>(`/workers/${editingWorker.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          label: formString(data, "label"),
          profile_kind: formString(data, "profile_kind") || editingWorker.profile_kind,
          email: formString(data, "email"),
          phone: formString(data, "phone"),
          note: formString(data, "note"),
        }),
      });
      setEditingWorker(null);
      setWorkspace((current) => current ? {
        ...current,
        worker_profiles: current.worker_profiles?.map((worker) => worker.id === updated.id ? updated : worker),
      } : current);
      load();
      onChanged();
      notify({ kind: "success", message: "Dane majstra/ekipy zapisane." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zapisać majstra/ekipy" });
    } finally {
      setBusy(false);
    }
  }

  async function deactivateWorker(worker: WorkerProfile) {
    const warning = worker.assigned_projects.length > 0
      ? "Ten majster/ekipa ma przypisane zlecenia. Dezaktywacja odepnie wykonawcę od tych zleceń. Kontynuować?"
      : "Dezaktywować tego majstra/ekipę?";
    if (!window.confirm(warning)) return;
    setBusy(true);
    try {
      await api(`/workers/${worker.id}`, { method: "DELETE" });
      load();
      onChanged();
      notify({ kind: "success", message: "Majster/ekipa została dezaktywowana." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się dezaktywować majstra/ekipy" });
    } finally {
      setBusy(false);
    }
  }

  async function activateWorker(worker: WorkerProfile) {
    setBusy(true);
    try {
      await api(`/workers/${worker.id}/activate`, { method: "POST" });
      load();
      onChanged();
      notify({ kind: "success", message: "Majster/ekipa została aktywowana ponownie." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się aktywować majstra/ekipy" });
    } finally {
      setBusy(false);
    }
  }

  if (!workspace) {
    return <section className="panel"><div className="loading-screen"><span className="spinner" /> Ładowanie firmy...</div></section>;
  }

  const workers = workspace.worker_profiles || [];
  const crews = workers.filter((worker) => worker.profile_kind === "crew");
  const craftsmen = workers.filter((worker) => worker.profile_kind !== "crew");

  return (
    <>
      <section className="panel company-team-panel">
        <div className="company-team-header">
          <div>
            <span className="eyebrow">Typ konta: Szef firmy</span>
            <h2>{workspace.name}</h2>
            <p>{workspace.description || "Jedna firma testowa do zarządzania majstrami i ekipami."}</p>
          </div>
          <div className="company-team-meta">
            <span>{workspace.phone || "Telefon nieuzupełniony"}</span>
            <span>{workspace.address || "Adres nieuzupełniony"}</span>
          </div>
        </div>
        <details className="company-details">
          <summary>Dane firmy / edytuj</summary>
          <form className="form-stack" onSubmit={saveCompany}>
            <label>Nazwa firmy<input name="name" defaultValue={workspace.name} required /></label>
            <label>Opis<textarea name="description" rows={3} defaultValue={workspace.description} /></label>
            <div className="form-row">
              <label>Telefon<input name="phone" defaultValue={workspace.phone} /></label>
              <label>Adres<input name="address" defaultValue={workspace.address} /></label>
            </div>
            <Button type="submit" busy={busy}>Zapisz dane firmy</Button>
          </form>
        </details>
      </section>

      <section className="team-management-grid">
        <div className="panel team-management-section">
          <div className="panel__header">
            <div>
              <h2>Zarządzaj ekipami</h2>
              <p>Ekipy wykonawcze przypisujesz później do zleceń.</p>
            </div>
            <span className="team-count">{crews.length}</span>
          </div>
          <div className="team-worker-list">
            {crews.length === 0 ? <p className="empty-note">Brak ekip po resecie lokalnej bazy.</p> : crews.map((worker) => (
              <TeamWorkerCard key={worker.id} worker={worker} busy={busy} onEdit={setEditingWorker} onDeactivate={deactivateWorker} onActivate={activateWorker} />
            ))}
          </div>
        </div>

        <div className="panel team-management-section">
          <div className="panel__header">
            <div>
              <h2>Zarządzaj pojedynczymi majstrami</h2>
              <p>Stali majstrowie i konta wykonawców firmy.</p>
            </div>
            <span className="team-count">{craftsmen.length}</span>
          </div>
          <div className="team-worker-list">
            {craftsmen.length === 0 ? <p className="empty-note">Brak pojedynczych majstrów.</p> : craftsmen.map((worker) => (
              <TeamWorkerCard key={worker.id} worker={worker} busy={busy} onEdit={setEditingWorker} onDeactivate={deactivateWorker} onActivate={activateWorker} />
            ))}
          </div>
        </div>
      </section>

      {(workspace.worker_links?.length || 0) > 0 && (
        <section className="panel team-management-section">
          <div className="panel__header"><div><h2>Linki tymczasowe</h2><p>Link-only przypisane do konkretnych zleceń.</p></div></div>
          <div className="member-list worker-link-list">
            {workspace.worker_links?.map((link) => (
              <article key={link.id}>
                <span>{link.label.slice(0, 2).toUpperCase()}</span>
                <div>
                  <strong>{link.label}</strong>
                  <small>{link.email || "Bez e-maila"} · {link.project_name || "Zlecenie"} · {link.account_type === "account" ? "konto/email" : "link-only"}</small>
                </div>
                <b>{link.revoked_at ? "Odwołany" : "Aktywny"}</b>
              </article>
            ))}
          </div>
        </section>
      )}

      <section className="panel add-worker-panel">
        <form className="form-stack" onSubmit={createWorker}>
          <h3>Dodaj majstra / ekipę</h3>
          <p className="form-intro">
            Podanie e-maila oznacza zaproszenie do stałego konta po potwierdzeniu kodem.
            Bez e-maila dodasz wykonawcę do listy, a link tymczasowy wyślesz z poziomu zlecenia.
          </p>
          <label>Typ<select name="profile_kind" defaultValue="craftsman"><option value="craftsman">Majster</option><option value="crew">Ekipa</option></select></label>
          <label>Nazwa<input name="label" required placeholder="np. Mieciu hydraulik albo Ekipa Kowalskiego" /></label>
          <div className="form-row">
            <label>E-mail opcjonalnie<input type="email" name="email" placeholder="Możesz zostawić puste" /></label>
            <label>Telefon opcjonalnie<input name="phone" /></label>
          </div>
          <label>Notatka opcjonalnie<textarea name="note" rows={2} placeholder="np. łazienki, instalacje, wykończenia" /></label>
          <Button type="submit" busy={busy} icon="plus">Dodaj majstra / ekipę</Button>
        </form>
        {invitationUrl && <div className="share-result"><input value={invitationUrl} readOnly /><Button variant="secondary" onClick={() => void copyToClipboard(invitationUrl)}>Kopiuj link</Button></div>}
      </section>

      {editingWorker && (
        <Modal title="Edytuj majstra / ekipę" onClose={() => setEditingWorker(null)}>
          <form className="form-stack" onSubmit={saveWorker}>
            <label>Typ<select name="profile_kind" defaultValue={editingWorker.profile_kind}><option value="craftsman">Majster / pojedynczy wykonawca</option><option value="crew">Ekipa</option></select></label>
            <label>Nazwa<input name="label" defaultValue={editingWorker.label} required autoFocus /></label>
            <label>E-mail opcjonalnie<input type="email" name="email" defaultValue={editingWorker.email} placeholder="Możesz zostawić puste" /></label>
            <label>Telefon<input name="phone" defaultValue={editingWorker.phone} /></label>
            <label>Notatka<textarea name="note" rows={3} defaultValue={editingWorker.note} /></label>
            <Button type="submit" busy={busy}>Zapisz</Button>
          </form>
        </Modal>
      )}
    </>
  );
}

function TeamPage({
  user,
  onUserUpdated,
  notify,
}: {
  user: User;
  onUserUpdated: (user: User) => void;
  notify: (toast: Toast) => void;
}) {
  const [showCreate, setShowCreate] = useState(false);
  const [selectedWorkspace, setSelectedWorkspace] = useState<string | null>(null);

  async function refreshUser() {
    onUserUpdated(await api<User>("/me"));
  }

  async function createWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const workspace = await api<Workspace>("/workspaces", {
        method: "POST",
        body: JSON.stringify({
          name: data.get("name"),
          kind: user.profile_type === "investor" ? "personal" : "company",
          description: data.get("description"),
          phone: data.get("phone"),
          address: data.get("address"),
        }),
      });
      await refreshUser();
      setShowCreate(false);
      setSelectedWorkspace(workspace.id);
      notify({ kind: "success", message: "Firma została utworzona. Możesz od razu dodać majstrów." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się utworzyć firmy" });
    }
  }
  const primaryTeamAction = user.profile_type === "investor"
    ? "Dodaj wykonawcę"
    : user.workspaces.length > 0
      ? "Dodaj majstra / ekipę"
      : "Utwórz firmę";
  function openTeamAction() {
    if (user.profile_type === "company_owner" && user.workspaces.length > 0) {
      setSelectedWorkspace(user.workspaces[0].id);
      return;
    }
    setShowCreate(true);
  }
  if (user.profile_type === "company_owner" && user.workspaces.length > 0) {
    return (
      <div className="page">
        <header className="page-header">
          <div>
            <span className="eyebrow">Firma i ekipy</span>
            <h1>Firma i majstrowie</h1>
            <p>Zarządzaj ekipami i pojedynczymi majstrami bez wybierania wielu firm.</p>
          </div>
        </header>
        <CompanyTeamPanel workspaceId={user.workspaces[0].id} onChanged={refreshUser} notify={notify} />
      </div>
    );
  }
  return (
    <div className="page">
      <header className="page-header"><div><span className="eyebrow">{user.profile_type === "investor" ? "Wykonawcy" : "Firma i ekipy"}</span><h1>{user.profile_type === "investor" ? "Wykonawcy" : "Firma i majstrowie"}</h1><p>{user.profile_type === "investor" ? "Dodawaj wykonawców, wybieraj ich przy zleceniu i wysyłaj im link do postępu." : "Edytuj dane firmy, dodawaj majstrów i wysyłaj im link do logowania."}</p></div><Button icon="plus" onClick={openTeamAction}>{primaryTeamAction}</Button></header>
      <section className="panel">
        {user.workspaces.length === 0 ? <EmptyState icon="users" title={user.profile_type === "investor" ? "Dodaj pierwszego wykonawcę" : "Dodaj swoją firmę"} text={user.profile_type === "investor" ? "Utworzysz listę wykonawców do przypisywania przy zleceniach." : "Po utworzeniu od razu zaprosisz majstrów i wyślesz im link do logowania."}><Button onClick={() => setShowCreate(true)}>Dodaj teraz</Button></EmptyState> : <div className="workspace-grid">{user.workspaces.map((workspace) => <button onClick={() => setSelectedWorkspace(workspace.id)} key={workspace.id}><span><Icon name="users" /></span><h3>{workspace.name}</h3><p>{workspace.description || (user.profile_type === "investor" ? "Kliknij, aby edytować wykonawców." : "Kliknij, aby edytować i dodać majstrów.")}</p><small>Twoja rola: {workspace.role}</small></button>)}</div>}
      </section>
      {showCreate && <Modal title={user.profile_type === "investor" ? "Nowa lista wykonawców" : "Nowa firma"} onClose={() => setShowCreate(false)}><form className="form-stack" onSubmit={createWorkspace}><label>Nazwa<input name="name" required autoFocus /></label><label>Opis<textarea name="description" rows={3} /></label><div className="form-row"><label>Telefon<input name="phone" /></label><label>Adres<input name="address" /></label></div><Button type="submit">{user.profile_type === "investor" ? "Utwórz listę wykonawców" : "Utwórz i dodaj majstrów"}</Button></form></Modal>}
      {selectedWorkspace && <WorkspaceModal workspaceId={selectedWorkspace} onClose={() => setSelectedWorkspace(null)} onChanged={refreshUser} notify={notify} />}
    </div>
  );
}

function SettingsPage({
  user,
  onUpdated,
  onLogout,
  notify,
}: {
  user: User;
  onUpdated: (user: User) => void;
  onLogout: () => void;
  notify: (toast: Toast) => void;
}) {
  const [admin, setAdmin] = useState<any>(null);
  useEffect(() => {
    if (user.is_admin) api("/admin/overview").then(setAdmin).catch(() => null);
  }, [user.is_admin]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const updated = await api<User>("/me", {
        method: "PATCH",
        body: JSON.stringify({
          name: data.get("name"),
          phone: data.get("phone"),
          locale: "pl",
          preferred_mode: data.get("preferred_mode"),
        }),
      });
      onUpdated(updated);
      notify({ kind: "success", message: "Profil zapisany" });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zapisać profilu" });
    }
  }
  return (
    <div className="page">
      <header className="page-header"><div><span className="eyebrow">Konto</span><h1>Ustawienia</h1><p>Uzupełnij dane widoczne przy wpisach i raportach.</p></div></header>
      <section className="panel settings-panel">
        <form className="form-stack" onSubmit={submit}>
          <label>Imię i nazwisko<input name="name" defaultValue={user.name} placeholder="Jan Kowalski" /></label>
          <label>E-mail<input value={user.email} disabled /></label>
          <label>Telefon<input name="phone" defaultValue={user.phone} placeholder="+48 600 000 000" /></label>
          {user.profile_type === "independent_contractor" && (
            <label>Domyślny widok zlecenia<select name="preferred_mode" defaultValue={user.preferred_mode}><option value="field">Terenowy / prosty</option><option value="expanded">Rozbudowany</option></select></label>
          )}
          <div className="beta-box"><Icon name="check" /><div><strong>Dostęp testowy aktywny</strong><p>Twoje konto ma bezpłatny dostęp do pilotażu Pan Majster.</p></div></div>
          <Button type="submit">Zapisz profil</Button>
          <Button type="button" variant="danger" onClick={onLogout}>Wyloguj się z aplikacji</Button>
        </form>
      </section>
      {user.is_admin && (
        <section className="panel admin-panel">
          <div className="panel__header"><div><h2>Panel administratora</h2><p>Testerzy, projekty i kolejka zadań.</p></div></div>
          {!admin ? <div className="loading-screen"><span className="spinner" /> Ładowanie danych...</div> : <>
            <div className="admin-stats"><span>Użytkownicy <strong>{admin.counts.users}</strong></span><span>Projekty <strong>{admin.counts.projects}</strong></span><span>Wpisy <strong>{admin.counts.entries}</strong></span><span>Pliki <strong>{admin.counts.media}</strong></span></div>
            <div className="admin-users">{admin.users.map((item: User) => <article key={item.id}><div><strong>{item.name || item.email}</strong><small>{item.email}</small></div><span>{item.is_admin ? "Administrator" : "Tester"}</span></article>)}</div>
          </>}
        </section>
      )}
    </div>
  );
}

function ImageLightbox({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  useEffect(() => {
    const close = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    addEventListener("keydown", close);
    return () => removeEventListener("keydown", close);
  }, [onClose]);
  return (
    <div className="lightbox" onClick={onClose}>
      <button onClick={onClose} aria-label="Zamknij podgląd"><Icon name="close" /></button>
      <img src={src} alt={alt} onClick={(event) => event.stopPropagation()} />
    </div>
  );
}

function PublicProject({ token }: { token: string }) {
  const [data, setData] = useState<any>(null);
  const [requiresPin, setRequiresPin] = useState(false);
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [lightbox, setLightbox] = useState<{ src: string; alt: string } | null>(null);

  const load = useCallback(async (providedPin?: string) => {
    const activePin = providedPin ?? pin;
    try {
      const result = await api<any>(`/public/projects/${token}${activePin ? `?pin=${encodeURIComponent(activePin)}` : ""}`);
      setRequiresPin(result.requires_pin && !result.project);
      if (result.project) setData(result);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Link klienta jest niedostępny");
    }
  }, [pin, token]);

  useEffect(() => { void load(""); }, [token]);
  useEffect(() => {
    if (!data?.project) return;
    const timer = window.setInterval(() => void load(), 15000);
    return () => clearInterval(timer);
  }, [data?.project, load]);

  if (requiresPin) {
    return <div className="public-page"><Logo /><form className="pin-card" onSubmit={(event) => { event.preventDefault(); void load(pin); }}><Icon name="clipboard" size={42} /><h1>Zlecenie chronione</h1><p>Wpisz PIN otrzymany od osoby prowadzącej zlecenie.</p><input value={pin} onChange={(event) => setPin(event.target.value)} inputMode="numeric" autoFocus /><Button type="submit">Otwórz zlecenie</Button>{error && <p className="form-error">{error}</p>}</form></div>;
  }
  if (!data?.project) return <div className="public-page"><Logo /><div className="loading-screen">{error || "Ładowanie zlecenia..."}</div></div>;

  const project = data.project as Project;
  const entries = data.entries as Entry[];
  const reports = data.reports as Report[];
  const withPin = (url: string) => `${url}${pin ? `?pin=${encodeURIComponent(pin)}` : ""}`;
  return (
    <div className="client-project">
      <header>
        <Logo />
        <div>
          <small>STAŁY PODGLĄD ZLECENIA</small>
          <h1>{project.name}</h1>
          <p>{project.address || project.client_name}</p>
        </div>
        <Button variant="secondary" icon="sync" onClick={() => void load()}>Odśwież</Button>
      </header>
      <main>
        <section className="client-overview panel">
          <div>
            <span className={`status status--${project.status}`}>{statusLabels[project.status]}</span>
            <h2>Postęp prac</h2>
            <p>{project.description || "Tutaj pojawiają się zdjęcia, opisy, problemy i kolejne raporty."}</p>
          </div>
          <div className="simple-stages">
            {project.stages?.map((stage, index) => <div className={`simple-stage simple-stage--${stage.status}`} key={stage.id}><span>{stage.status === "completed" ? "✓" : index + 1}</span><div><strong>{stage.title}</strong><small>{stage.status === "completed" ? "Zakończony" : stage.status === "active" ? "W trakcie" : "Przed nami"}</small></div></div>)}
          </div>
        </section>
        <div className="client-layout">
          <section className="client-timeline panel">
            <div className="panel__header"><div><h2>Oś czasu zlecenia</h2><p>Nowe materiały pojawiają się tutaj automatycznie.</p></div></div>
            {entries.length === 0 ? <EmptyState icon="camera" title="Jeszcze bez aktualizacji" text="Pierwsze zdjęcia i opisy pojawią się tutaj." /> : (
              <div className="timeline">
                {entries.map((entry) => (
                  <article className={`timeline-entry timeline-entry--${entry.kind}`} key={entry.id}>
                    <span className="timeline-entry__marker"><Icon name={entry.kind === "problem" ? "alert" : "camera"} /></span>
                    <div className="timeline-entry__body">
                      <header><div><strong>{entry.kind === "problem" ? "Zgłoszony problem" : "Aktualizacja prac"}</strong><span>{new Intl.DateTimeFormat("pl", { dateStyle: "medium", timeStyle: "short" }).format(new Date(entry.occurred_at))}</span></div></header>
                      {entry.stage && <span className="stage-label">{entry.stage.title}</span>}
                      {(entry.body || entry.transcript) && <p>{entry.body || entry.transcript}</p>}
                      {entry.kind === "problem" && <span className={`problem-toggle problem-toggle--${entry.problem_status}`}>{entry.problem_status === "resolved" ? "Problem rozwiązany" : "Problem otwarty"}</span>}
                      {entry.media.some((asset) => asset.kind === "image") && <div className="media-grid">{entry.media.filter((asset) => asset.kind === "image").map((asset) => { const src = withPin(asset.url); return <button type="button" className="media-button" onClick={() => setLightbox({ src, alt: asset.original_name })} key={asset.id}><img src={src} alt={asset.original_name} loading="lazy" /></button>; })}</div>}
                      {entry.media.filter((asset) => asset.kind === "audio").map((asset) => <audio controls src={withPin(asset.url)} key={asset.id} />)}
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
          <aside className="client-reports panel">
            <div className="panel__header"><div><h2>Raporty PDF</h2><p>Wszystkie opublikowane raporty.</p></div></div>
            {reports.length === 0 ? <EmptyState icon="report" title="Brak raportów" text="Raporty pojawią się tutaj po publikacji." /> : reports.map((report) => <a className="client-report-link" href={withPin(`/api/public/projects/${token}/reports/${report.id}/pdf`)} target="_blank" key={report.id}><Icon name="report" /><div><strong>{report.title}</strong><small>{report.published_at ? new Intl.DateTimeFormat("pl").format(new Date(report.published_at)) : ""}</small></div><span>Otwórz PDF</span></a>)}
          </aside>
        </div>
      </main>
      <footer><Logo /><span>Zdjęcie. Głos. Raport.</span></footer>
      {lightbox && <ImageLightbox src={lightbox.src} alt={lightbox.alt} onClose={() => setLightbox(null)} />}
    </div>
  );
}

function InvitePage({
  token,
  onSuccess,
}: {
  token: string;
  onSuccess: (user: User) => void;
}) {
  const [details, setDetails] = useState<{ email: string; kind: string; role: string; project_name?: string; workspace_name?: string } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<{ email: string; kind: string; role: string; project_name?: string; workspace_name?: string }>(`/invitations/${token}`).then(setDetails).catch((reason) => setError(reason instanceof Error ? reason.message : "Zaproszenie jest niedostępne"));
  }, [token]);
  if (!details) return <div className="public-page"><Logo /><div className="loading-screen">{error || "Sprawdzamy zaproszenie..."}</div></div>;
  const roleLabel = details.kind === "workspace"
    ? "wykonawca / majster / członek ekipy"
    : details.role === "viewer"
      ? "podgląd zlecenia"
      : "wykonawca / majster";
  return (
    <div className="public-page">
      <Logo />
      <div className="invite-card">
        <Icon name="users" size={44} />
        <h1>Masz zaproszenie jako {roleLabel}</h1>
        <p>
          {details.workspace_name ? `${details.workspace_name} zaprasza Cię do Pan Majster. ` : ""}
          {details.project_name ? `Zlecenie: ${details.project_name}. ` : ""}
          Potwierdź adres <strong>{details.email}</strong> kodem z poczty. Po potwierdzeniu dostęp pojawi się automatycznie.
        </p>
      </div>
      <AuthModal initialEmail={details.email} onClose={() => navigate("/")} onSuccess={onSuccess} />
    </div>
  );
}

function PublicReport({ token }: { token: string }) {
  const [report, setReport] = useState<any>(null);
  const [requiresPin, setRequiresPin] = useState(false);
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
  const [lightbox, setLightbox] = useState<string | null>(null);
  const load = useCallback(async (providedPin?: string) => {
    try {
      const data = await api<any>(`/public/reports/${token}${providedPin ? `?pin=${encodeURIComponent(providedPin)}` : ""}`);
      setRequiresPin(data.requires_pin && !data.report);
      setReport(data);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Raport jest niedostępny");
    }
  }, [token]);
  useEffect(() => { load(); }, [load]);
  if (requiresPin) return <div className="public-page"><Logo /><form className="pin-card" onSubmit={(event) => { event.preventDefault(); load(pin); }}><Icon name="report" size={42} /><h1>Raport chroniony</h1><p>Wpisz kod PIN otrzymany od osoby udostępniającej raport.</p><input value={pin} onChange={(e) => setPin(e.target.value)} inputMode="numeric" autoFocus /><Button type="submit">Otwórz raport</Button>{error && <p className="form-error">{error}</p>}</form></div>;
  if (!report?.report) return <div className="public-page"><Logo /><div className="loading-screen">{error || "Ładowanie raportu..."}</div></div>;
  const item = report.report as Report;
  return (
    <div className="public-report">
      <header><Logo /><div><small>RAPORT Z REALIZACJI</small><h1>{item.title}</h1><p>{report.project.name} · {report.project.address}</p></div><a className="button button--secondary" href={`/api/public/reports/${token}/pdf${pin ? `?pin=${encodeURIComponent(pin)}` : ""}`}>Pobierz PDF</a></header>
      <main>
        <section className="public-report__summary"><span><Icon name="check" /></span><div><h2>Podsumowanie</h2><p>{item.content.summary}</p></div></section>
        {item.content.stages?.map((stage, index) => <section className="public-report__stage" key={stage.title}><span className="stage-number">{index + 1}</span><div><h2>{stage.title}</h2>{stage.entries.map((entry) => <article key={entry.entry_id}><small>{entry.date}</small><p>{entry.text}</p>{entry.media_ids && entry.media_ids.length > 0 && <div className="media-grid">{entry.media_ids.map((id) => { const src = `/api/public/reports/${token}/media/${id}${pin ? `?pin=${encodeURIComponent(pin)}` : ""}`; return <button type="button" className="media-button" onClick={() => setLightbox(src)} key={id}><img src={src} alt="Zdjęcie z raportu" /></button>; })}</div>}</article>)}</div></section>)}
      </main>
      <footer>Raport wygenerowany w aplikacji Pan Majster · Zdjęcie. Głos. Raport.</footer>
      {lightbox && <ImageLightbox src={lightbox} alt="Zdjęcie z raportu" onClose={() => setLightbox(null)} />}
    </div>
  );
}

function PublicPortfolio({ slug }: { slug: string }) {
  const [data, setData] = useState<any>(null);
  useEffect(() => { api(`/portfolio/${slug}`).then(setData).catch(() => setData({ error: true })); }, [slug]);
  return (
    <div className="portfolio-page">
      <header><Logo /><div><span className="eyebrow">Portfolio realizacji</span><h1>Nasza robota mówi sama za siebie.</h1></div></header>
      <main>{data?.projects?.map((project: any) => <article key={project.id}><div className="portfolio-images">{project.images.map((image: any) => <img src={image.url} key={image.id} alt="" />)}</div><h2>{project.name}</h2><p>{project.portfolio_summary || project.description}</p><span>{project.address}</span></article>) || <div className="loading-screen">{data?.error ? "Portfolio jest niedostępne." : "Ładowanie..."}</div>}</main>
    </div>
  );
}

export default function App() {
  const [currentRoute, setCurrentRoute] = useState<Route>(route);
  const [user, setUser] = useState<User | null>(null);
  const [authOpen, setAuthOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [projects, setProjects] = useState<Project[]>([]);
  const [section, setSection] = useState("home");
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);
  const [queueCount, setQueueCount] = useState(0);
  const toastTimer = useRef<number | null>(null);

  const notify = useCallback((next: Toast) => {
    setToast(next);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 4000);
  }, []);

  const refreshQueue = useCallback(async () => setQueueCount((await queuedEntries()).length), []);
  const loadProjects = useCallback(async () => {
    if (!user) return;
    const result = await api<Project[]>("/projects");
    const details = await Promise.all(result.map(async (project) => {
      try { return await api<Project>(`/projects/${project.id}`); } catch { return project; }
    }));
    setProjects(details);
  }, [user]);

  const syncQueue = useCallback(async () => {
    if (!navigator.onLine) return;
    const queue = await queuedEntries();
    for (const queued of queue) {
      try {
        const entry = await api<Entry>(`/projects/${queued.projectId}/entries`, {
          method: "POST",
          body: JSON.stringify(queued.payload),
        }, queued.guestToken);
        for (const file of queued.files) {
          const body = new FormData();
          body.append("file", new File([file.blob], file.name, { type: file.type }));
          body.append("client_ref", file.clientRef);
          body.append(
            "purpose",
            file.name.startsWith("opis-")
              ? "voice_description"
              : file.name.startsWith("notatka-")
                ? "voice_note"
                : "attachment",
          );
          await api(`/entries/${entry.id}/media`, { method: "POST", body }, queued.guestToken);
        }
        await deleteQueuedEntry(queued.id);
      } catch {
        break;
      }
    }
    await refreshQueue();
  }, [refreshQueue]);

  useEffect(() => {
    const handler = () => setCurrentRoute(route());
    addEventListener("popstate", handler);
    return () => removeEventListener("popstate", handler);
  }, []);

  useEffect(() => {
    refreshQueue();
    addEventListener("online", syncQueue);
    return () => removeEventListener("online", syncQueue);
  }, [refreshQueue, syncQueue]);

  useEffect(() => {
    api<User>("/me")
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadProjects(); }, [loadProjects]);

  useEffect(() => {
    if (currentRoute.kind === "app" && !loading && !user) setAuthOpen(true);
  }, [currentRoute, loading, user]);

  useEffect(() => {
    if (currentRoute.kind === "invite" && user) navigate("/app");
  }, [currentRoute, user]);

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    setUser(null);
    setProjects([]);
    navigate("/");
  }

  if (currentRoute.kind === "client") return <PublicProject token={currentRoute.token} />;
  if (currentRoute.kind === "report") return <PublicReport token={currentRoute.token} />;
  if (currentRoute.kind === "portfolio") return <PublicPortfolio slug={currentRoute.slug} />;
  if (currentRoute.kind === "guest") {
    return <GuestEntry token={currentRoute.token} notify={notify} onQueue={refreshQueue} />;
  }
  if (currentRoute.kind === "invite" && !user) {
    return <InvitePage token={currentRoute.token} onSuccess={(next) => { setUser(next); navigate("/app"); }} />;
  }

  const marketing = currentRoute.kind === "marketing" && !user;
  if (marketing) {
    return <><Marketing onLogin={() => setAuthOpen(true)} />{authOpen && <AuthModal onClose={() => setAuthOpen(false)} onSuccess={(next) => { setUser(next); setAuthOpen(false); navigate("/app"); }} />}{toast && <ToastView toast={toast} />}</>;
  }
  if (loading || !user) {
    return <><div className="splash"><img src="/brand/app-icon.png" alt="Pan Majster" /><span className="spinner" /></div>{authOpen && <AuthModal onClose={() => { setAuthOpen(false); navigate("/"); }} onSuccess={(next) => { setUser(next); setAuthOpen(false); navigate("/app"); }} />}</>;
  }
  if (!user.profile_type) {
    return <Onboarding onComplete={(next) => { setUser(next); setSection("home"); navigate("/app"); }} onBack={logout} />;
  }

  const body = selectedProject ? (
    <ProjectView projectId={selectedProject.id} user={user} onUserUpdated={setUser} onBack={() => setSelectedProject(null)} notify={notify} onQueue={refreshQueue} />
  ) : section === "projects" ? (
    <ProjectsPage user={user} projects={projects} onProject={setSelectedProject} onCreate={() => setCreateOpen(true)} />
  ) : section === "reports" ? (
    <ReportsPage projects={projects} onOpen={setSelectedProject} />
  ) : section === "team" ? (
    <TeamPage user={user} onUserUpdated={setUser} notify={notify} />
  ) : section === "settings" ? (
    <SettingsPage user={user} onUpdated={setUser} onLogout={logout} notify={notify} />
  ) : (
    <Dashboard user={user} projects={projects} onProject={setSelectedProject} onCreate={() => setCreateOpen(true)} />
  );

  return (
    <>
      <Shell user={user} active={selectedProject ? "projects" : section} onNavigate={(next) => { setSelectedProject(null); setSection(next); }} onLogout={logout} queueCount={queueCount}>
        {body}
      </Shell>
      {createOpen && <CreateProjectModal user={user} onClose={() => setCreateOpen(false)} onCreated={(project) => { setCreateOpen(false); setProjects((current) => [project, ...current]); setSelectedProject(project); notify({ kind: "success", message: "Zlecenie utworzone" }); }} />}
      {toast && <ToastView toast={toast} />}
    </>
  );
}

function GuestEntry({ token, notify, onQueue }: { token: string; notify: (toast: Toast) => void; onQueue: () => void }) {
  const [details, setDetails] = useState<{ project_id: string; project_name: string; label: string; kind: string; account_type: string; permission: string } | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    api<{ project_id: string; project_name: string; label: string; kind: string; account_type: string; permission: string }>(`/guest/${token}`)
      .then(setDetails)
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Link jest nieaktywny"));
  }, [token]);
  if (error) return <div className="public-page"><Logo /><EmptyState icon="alert" title="Link jest nieaktywny" text={error} /></div>;
  if (!details) return <div className="splash"><img src="/brand/app-icon.png" alt="Pan Majster" /><span className="spinner" /></div>;
  if (!accepted) {
    return (
      <div className="public-page">
        <Logo />
        <div className="invite-card">
          <Icon name="clipboard" size={44} />
          <h1>Zaproszenie do zlecenia</h1>
          <p>
            Zaproszono Cię jako <strong>wykonawcę / majstra / ekipę</strong>.
            Zlecenie: <strong>{details.project_name}</strong>. Podpis linku: <strong>{details.label}</strong>.
          </p>
          <p>Ten link działa bez e-maila, kodu i logowania. Daje dostęp tylko do tego jednego zlecenia.</p>
          <Button onClick={() => setAccepted(true)} icon="clipboard">Wejdź do zlecenia</Button>
        </div>
      </div>
    );
  }
  return <><ProjectView projectId={details.project_id} guestToken={token} onBack={() => setAccepted(false)} notify={notify} onQueue={onQueue} /></>;
}

function ToastView({ toast }: { toast: Toast }) {
  return <div className={`toast toast--${toast.kind}`}><Icon name={toast.kind === "error" ? "alert" : toast.kind === "success" ? "check" : "sync"} /><span>{toast.message}</span></div>;
}
