import {
  FormEvent,
  ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  canCreateProject,
  canManagePeople,
  canSeeTeamPanel,
  isCompanyOwner,
  isCompanyWorker,
  isIndependentContractor,
  isInvestor,
} from "./access";
import { AppShell } from "./AppShell";
import { ManageProjectModal } from "./ManageProjectModal";
import { ApiError, api } from "./api";
import { Icon } from "./icons";
import {
  deleteQueuedEntry,
  queueEntry,
  queuedEntries,
  type QueuedEntry,
} from "./offline";
import {
  peopleLabelsForUser,
  profileLabels,
  workerKindLabel,
  workerKindLabelForUser,
} from "./roleLabels";
import { visibleSectionForUser } from "./RoleAwareSidebar";
import type { ClientLink, Entry, Project, Report, Stage, User, WorkerProfile, Workspace } from "./types";
import { useUiMode, type UiMode } from "./useUiMode";

type Toast = { kind: "success" | "error" | "info"; message: string };
type EntryTextTarget = "description" | "note";
type EntryModalState = { kind: "update" | "problem"; mode: "photo" | "audio" | "text" };
type SpeechRecognitionState = "idle" | "listening" | "unsupported" | "error" | "manual";
type SpeechRecognitionInfo = {
  target: EntryTextTarget | null;
  state: SpeechRecognitionState;
  message: string;
};
type SpeechRecognitionResultLike = {
  isFinal: boolean;
  0: { transcript: string };
};
type SpeechRecognitionEventLike = {
  resultIndex: number;
  results: {
    length: number;
    [index: number]: SpeechRecognitionResultLike;
  };
};
type SpeechRecognitionInstance = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: (() => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort?: () => void;
};
type SpeechRecognitionConstructor = new () => SpeechRecognitionInstance;

const testAccounts = [
  {
    label: "Demo Szef firmy",
    email: "szef@majster.pl",
    description: "pełna firma, majstrowie, ekipy, zlecenia, raporty",
  },
  {
    label: "Demo Inwestor",
    email: "inwestor@majster.pl",
    description: "inwestycje, wykonawcy, historia remontów i usług",
  },
  {
    label: "Demo Samodzielny majster",
    email: "samodzielny@majster.pl",
    description: "własne zlecenia, postęp, raporty",
  },
  {
    label: "Demo Majster firmy - glazurnik",
    email: "pracownik@majster.pl",
    description: "przypisane zlecenia firmy",
  },
  {
    label: "Demo Majster firmy - hydraulik",
    email: "pracownik2@majster.pl",
    description: "przypisane zlecenia firmy",
  },
];

function workerOptionLabel(user: User | undefined, worker: WorkerProfile): string {
  return `${workerKindLabelForUser(user, worker)}: ${worker.label} - ${workerAccountLabel(worker)}`;
}

function workerAccountLabel(worker: WorkerProfile): string {
  if (!worker.active) return "dezaktywowany";
  if (worker.profile_kind === "crew" && !worker.email) return "ekipa link-only / bez e-maila";
  if (worker.account_status === "active") return "konto aktywne";
  if (worker.account_status === "pending_email") return "oczekuje na potwierdzenie e-mail";
  if (worker.account_type === "account") return "konto stałe / e-mail";
  return "link-only";
}

function defaultEntryStageId(project: Project): string {
  return (
    project.stages?.find((stage) => stage.title === "W trakcie realizacji")?.id ||
    project.stages?.find((stage) => stage.status === "active")?.id ||
    project.stages?.[0]?.id ||
    ""
  );
}

function projectStageProgress(project: Project): { completedCount: number; progress: number } {
  const stages = project.stages || [];
  if (!stages.length) return { completedCount: 0, progress: 0 };
  if (project.status === "completed") return { completedCount: stages.length, progress: 100 };
  const activeIndex = stages.findIndex((stage) => stage.status === "active");
  const completedCount = activeIndex >= 0
    ? activeIndex
    : stages.filter((stage) => stage.status === "completed").length;
  return {
    completedCount,
    progress: Math.floor((completedCount / stages.length) * 100),
  };
}

function activeProjectStage(project: Project): Stage | undefined {
  return (
    project.stages?.find((stage) => stage.status === "active") ||
    project.stages?.find((stage) => stage.status === "planned") ||
    project.stages?.[0]
  );
}

function projectStageLabel(project: Project): string {
  const active = activeProjectStage(project);
  if (!active) return "Etap nieustawiony";
  return active.title;
}

function stageStatusText(stage: Stage): string {
  if (stage.status === "completed") return "Ukończony";
  if (stage.status === "active") return "Aktualny etap";
  return "Oczekuje";
}

function formString(data: FormData, name: string): string {
  return String(data.get(name) || "").trim();
}

function formNullableString(data: FormData, name: string): string | null {
  return formString(data, name) || null;
}

function formMoneyString(data: FormData, name: string): string | null {
  const value = formString(data, name).replace(/\s/g, "").replace(",", ".");
  return value || null;
}

function formOptionalNumber(data: FormData, name: string): number | null {
  const value = formString(data, name);
  if (!value) return null;
  return Number(value);
}

function formatContractDate(value?: string | null): string {
  if (!value) return "";
  return new Intl.DateTimeFormat("pl").format(new Date(`${value}T00:00:00`));
}

function formatProjectActivityDate(value?: string | null): string {
  if (!value) return "";
  return new Intl.DateTimeFormat("pl", { day: "2-digit", month: "2-digit", year: "numeric" }).format(new Date(value));
}

function contractAmountLabel(project: Project): string {
  if (!project.contract_amount) return "";
  return `${project.contract_amount} ${project.contract_currency || "PLN"}`;
}

function contractTermRows(project: Project): Array<{ label: string; value: string }> {
  const rows: Array<{ label: string; value: string }> = [];
  const start = formatContractDate(project.planned_start_date);
  const end = formatContractDate(project.planned_end_date);
  const amount = contractAmountLabel(project);
  if (start) rows.push({ label: "Planowany start", value: start });
  if (end) rows.push({ label: "Planowany koniec", value: end });
  if (project.schedule_uncertainty_days !== null && project.schedule_uncertainty_days !== undefined) {
    rows.push({ label: "Niepewnosc terminu", value: `+/- ${project.schedule_uncertainty_days} dni` });
  }
  if (amount) rows.push({ label: "Kwota umowna", value: amount });
  return rows;
}

function projectListCopy(user: User) {
  if (user.profile_type === "investor") {
    return {
      eyebrow: "Twoje inwestycje",
      title: "Inwestycje / Zlecenia",
      description: "Kontroluj inwestycje, wykonawców i najważniejsze terminy.",
      createLabel: "Dodaj inwestycję",
      searchPlaceholder: "Szukaj inwestycji, wykonawcy lub adresu...",
      emptyTitle: "Dodaj pierwszą inwestycję",
      emptyText: "Inwestycja połączy wykonawcę, terminy, kwotę i historię postępu.",
    };
  }
  if (user.profile_type === "company_worker") {
    return {
      eyebrow: "Przypisane realizacje",
      title: "Moje zlecenia",
      description: "Zlecenia przypisane do Twojej pracy i historii postępu.",
      createLabel: "Dodaj zlecenie",
      searchPlaceholder: "Szukaj zlecenia, klienta lub adresu...",
      emptyTitle: "Brak przypisanych zleceń",
      emptyText: "Gdy szef firmy przypisze Ci zlecenie, pojawi się na tej liście.",
    };
  }
  if (user.profile_type === "independent_contractor") {
    return {
      eyebrow: "Twoje realizacje",
      title: "Moje zlecenia",
      description: "Terminy, kwoty, status i ostatni postęp w jednym widoku.",
      createLabel: "Dodaj zlecenie",
      searchPlaceholder: "Szukaj zlecenia, klienta lub adresu...",
      emptyTitle: "Dodaj pierwsze zlecenie",
      emptyText: "Zlecenie połączy terminy, kwotę i historię postępu.",
    };
  }
  return {
    eyebrow: "Wszystkie realizacje",
    title: "Zlecenia",
    description: "Terminy, wykonawcy, kwoty i statusy zleceń firmy.",
    createLabel: "Nowe zlecenie",
    searchPlaceholder: "Szukaj zlecenia, klienta, wykonawcy lub adresu...",
    emptyTitle: "Dodaj pierwsze zlecenie",
    emptyText: "Zlecenie połączy wykonawcę, terminy, kwotę i historię postępu.",
  };
}

function projectPartyLabel(user: User): string {
  if (user.profile_type === "investor") return "Wykonawca";
  if (user.profile_type === "independent_contractor") return "Realizuje";
  return "Majster / ekipa";
}

function projectPartyValue(user: User, project: Project): string {
  if (project.worker_profile?.label) return project.worker_profile.label;
  if (user.profile_type === "independent_contractor") return "Ty / Twoja firma";
  return "Nie przypisano";
}

function projectLastProgressLabel(project: Project): string {
  if (!project.entry_count) return "Brak wpisów";
  if (!project.updated_at) return `${project.entry_count} wpisów`;
  return `${project.entry_count} wpisów · ostatnio ${new Intl.DateTimeFormat("pl").format(new Date(project.updated_at))}`;
}

function projectActivityLabel(project: Project): string {
  const activity = formatProjectActivityDate(project.updated_at || project.created_at);
  return activity ? `Ostatnio ${activity}` : "Brak daty aktywności";
}

function ContractTermsPanel({ project }: { project: Project }) {
  const rows = contractTermRows(project);
  if (rows.length === 0) return null;
  return (
    <section className="contract-terms">
      <h3>Terminy i kwota</h3>
      <dl>
        {rows.map((row) => (
          <div key={row.label}>
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
      <p>{contractTermsDisclaimer}</p>
    </section>
  );
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
const contractTermsDisclaimer = "To informacja umowna. To nie jest faktura, platnosc ani wezwanie do zaplaty.";

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
  variant?: "primary" | "secondary" | "ghost" | "danger" | "success";
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
                <small>{account.description}</small>
              </button>
            ))}
            <small>Hasło kont demo: test1234. Wykonawcy bez e-maila nie mają konta - korzystają z linków do konkretnych zleceń.</small>
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
          planned_start_date: formNullableString(data, "planned_start_date"),
          planned_end_date: formNullableString(data, "planned_end_date"),
          schedule_uncertainty_days: formOptionalNumber(data, "schedule_uncertainty_days"),
          contract_amount: formMoneyString(data, "contract_amount"),
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
                  {workerOptionLabel(user, worker)}
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
        <div className="contract-fields">
          <div className="form-row">
            <label>Planowany start<input type="date" name="planned_start_date" /></label>
            <label>Planowany koniec<input type="date" name="planned_end_date" /></label>
          </div>
          <div className="form-row">
            <label>Niepewnosc terminu (+/- dni)<input type="number" name="schedule_uncertainty_days" min="0" step="1" placeholder="np. 3" /></label>
            <label>Kwota umowna (PLN)<input type="text" name="contract_amount" inputMode="decimal" placeholder="np. 12000" /></label>
          </div>
          <p className="form-note">{contractTermsDisclaimer}</p>
        </div>
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

function Dashboard({
  user,
  projects,
  onProject,
  onCreate,
  uiMode,
}: {
  user: User;
  projects: Project[];
  onProject: (project: Project) => void;
  onCreate: () => void;
  uiMode: UiMode;
}) {
  const simpleMode = uiMode === "simple";
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
      <div className={`stat-grid ${simpleMode ? "stat-grid--simple" : ""}`}>
        <article><span className="stat-icon stat-icon--blue"><Icon name="clipboard" /></span><div><small>Aktywne zlecenia</small><strong>{active.length}</strong></div></article>
        <article><span className="stat-icon stat-icon--red"><Icon name="alert" /></span><div><small>Otwarte problemy</small><strong>{problems}</strong></div></article>
        {!simpleMode && <article><span className="stat-icon stat-icon--green"><Icon name="check" /></span><div><small>Zakończone</small><strong>{projects.filter((p) => p.status === "completed").length}</strong></div></article>}
        {!simpleMode && <article><span className="stat-icon stat-icon--orange"><Icon name="users" /></span><div><small>Wszystkie projekty</small><strong>{projects.length}</strong></div></article>}
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
            {projects.slice(0, simpleMode ? 5 : 8).map((project) => (
              <button key={project.id} className={`project-row ${simpleMode ? "project-row--simple" : ""}`} onClick={() => onProject(project)}>
                <span className="project-row__icon"><Icon name="clipboard" /></span>
                <div className="project-row__main">
                  <strong>{project.name}</strong>
                  <span>{project.client_name || "Bez klienta"} · {project.address || "Bez adresu"}</span>
                  {!simpleMode && <small>{projectPartyLabel(user)}: {projectPartyValue(user, project)} · {projectActivityLabel(project)}</small>}
                </div>
                <span className={`status status--${project.status}`}>{statusLabels[project.status] || project.status}</span>
                {!simpleMode && <span className="project-row__meta">{formatContractDate(project.planned_end_date) || (project.role === "owner" ? "Właściciel" : "Współpraca")}</span>}
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
  uiMode,
  onUiModeChange,
  notify,
  onQueue,
  onChanged,
}: {
  user: User;
  projects: Project[];
  onProject: (project: Project) => void;
  onCreate: () => void;
  uiMode: UiMode;
  onUiModeChange: (mode: UiMode) => void;
  notify: (toast: Toast) => void;
  onQueue: () => void;
  onChanged: () => void;
}) {
  const [filter, setFilter] = useState("");
  const [viewFilter, setViewFilter] = useState<"all" | "open" | "history">("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [workerFilter, setWorkerFilter] = useState("all");
  const [sortBy, setSortBy] = useState<"newest" | "oldest" | "start" | "end" | "status">("newest");
  const simpleMode = uiMode === "simple";
  const canFilterWorkers = !simpleMode && canManagePeople(user);
  useEffect(() => {
    if (simpleMode && !["newest", "oldest"].includes(sortBy)) setSortBy("newest");
  }, [simpleMode, sortBy]);
  useEffect(() => {
    if (!canFilterWorkers && workerFilter !== "all") setWorkerFilter("all");
  }, [canFilterWorkers, workerFilter]);
  const copy = projectListCopy(user);
  const query = filter.trim().toLowerCase();
  const statusOrder: Record<string, number> = { assigned: 1, in_progress: 2, completed: 3 };
  const workerOptions = useMemo(() => {
    const seen = new Set<string>();
    return projects
      .filter((project) => project.worker_profile?.id && project.worker_profile?.label)
      .map((project) => project.worker_profile!)
      .filter((worker) => {
        if (seen.has(worker.id)) return false;
        seen.add(worker.id);
        return true;
      })
      .sort((left, right) => left.label.localeCompare(right.label, "pl"));
  }, [projects]);
  const visible = [...projects]
    .filter((item) => {
      const matchesQuery = `${item.name} ${item.client_name} ${item.address} ${item.worker_profile?.label || ""}`.toLowerCase().includes(query);
      const matchesView = viewFilter === "all" || (viewFilter === "open" ? item.status !== "completed" : item.status === "completed");
      const matchesStatus = statusFilter === "all" || item.status === statusFilter;
      const matchesWorker = !canFilterWorkers || workerFilter === "all" || item.worker_profile?.id === workerFilter;
      return matchesQuery && matchesView && matchesStatus && matchesWorker;
    })
    .sort((left, right) => {
      if (sortBy === "status") return (statusOrder[left.status] || 99) - (statusOrder[right.status] || 99);
      const dateValue = (project: Project) => {
        if (sortBy === "start") return project.planned_start_date ? `${project.planned_start_date}T00:00:00` : project.created_at;
        if (sortBy === "end") return project.planned_end_date ? `${project.planned_end_date}T00:00:00` : project.created_at;
        return project.updated_at || project.created_at;
      };
      const result = new Date(dateValue(left)).getTime() - new Date(dateValue(right)).getTime();
      return sortBy === "oldest" || sortBy === "start" || sortBy === "end" ? result : -result;
    });
  if (isCompanyWorker(user)) {
    return (
      <CompanyWorkerProjectsPage
        projects={projects}
        onProject={onProject}
        uiMode={uiMode}
        onUiModeChange={onUiModeChange}
        notify={notify}
        onQueue={onQueue}
        onChanged={onChanged}
      />
    );
  }
  return (
    <div className="page">
      <header className="page-header">
        <div><span className="eyebrow">{copy.eyebrow}</span><h1>{copy.title}</h1><p>{copy.description}</p></div>
        {canCreateProject(user) && <Button icon="plus" onClick={onCreate}>{copy.createLabel}</Button>}
      </header>
      <section className="panel">
        <div className="project-controls">
          <div className="list-tabs" role="tablist" aria-label="Widok zleceń">
            <button type="button" className={viewFilter === "all" ? "active" : ""} onClick={() => setViewFilter("all")}>Wszystkie</button>
            <button type="button" className={viewFilter === "open" ? "active" : ""} onClick={() => setViewFilter("open")}>Otwarte</button>
            <button type="button" className={viewFilter === "history" ? "active" : ""} onClick={() => setViewFilter("history")}>Historyczne</button>
          </div>
          <input type="search" placeholder={copy.searchPlaceholder} value={filter} onChange={(e) => setFilter(e.target.value)} />
          <div className="project-filter-row">
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="Filtr statusu">
              <option value="all">Wszystkie statusy</option>
              <option value="assigned">Zlecone</option>
              <option value="in_progress">W realizacji</option>
              <option value="completed">Zakończono</option>
            </select>
            {canFilterWorkers && workerOptions.length > 0 && (
              <select value={workerFilter} onChange={(event) => setWorkerFilter(event.target.value)} aria-label="Filtr wykonawcy">
                <option value="all">{isInvestor(user) ? "Wszyscy wykonawcy" : "Wszyscy majstrowie / ekipy"}</option>
                {workerOptions.map((worker) => (
                  <option key={worker.id} value={worker.id}>{worker.label}</option>
                ))}
              </select>
            )}
            <div className="project-sort-controls" aria-label="Sortowanie zleceń">
              <button type="button" className={sortBy === "newest" ? "active" : ""} onClick={() => setSortBy("newest")}>Najnowsze</button>
              <button type="button" className={sortBy === "oldest" ? "active" : ""} onClick={() => setSortBy("oldest")}>Najstarsze</button>
              {!simpleMode && <button type="button" className={sortBy === "start" ? "active" : ""} onClick={() => setSortBy("start")}>Data rozpoczęcia</button>}
              {!simpleMode && <button type="button" className={sortBy === "end" ? "active" : ""} onClick={() => setSortBy("end")}>Data zakończenia</button>}
              {!simpleMode && <button type="button" className={sortBy === "status" ? "active" : ""} onClick={() => setSortBy("status")}>Status</button>}
            </div>
          </div>
        </div>
        {projects.length === 0 ? (
          <EmptyState icon="clipboard" title={copy.emptyTitle} text={copy.emptyText}>
            {canCreateProject(user) && <Button onClick={onCreate} icon="plus">{copy.createLabel}</Button>}
          </EmptyState>
        ) : visible.length === 0 ? (
          <EmptyState icon="clipboard" title="Brak wyników" text="Zmień wyszukiwaną frazę, żeby zobaczyć pasujące pozycje." />
        ) : (
          <div className="project-list-cards">
            {visible.map((project) => (
              <article className="project-list-card" key={project.id}>
                <div className="project-list-card__top">
                  <span className="project-card__icon"><Icon name="clipboard" /></span>
                  <div className="project-list-card__identity">
                    <h3>{project.name}</h3>
                    <span>{project.client_name || "Bez klienta"} · {project.address || "Adres nieuzupełniony"}</span>
                  </div>
                  <div className="project-list-card__status">
                    <span className={`status project-status-badge status--${project.status}`}>{statusLabels[project.status] || project.status}</span>
                    <small>{projectActivityLabel(project)}</small>
                  </div>
                </div>
                <dl className={`project-meta-grid project-meta-grid--overview ${simpleMode ? "project-meta-grid--simple" : ""}`}>
                  <div><dt>{projectPartyLabel(user)}</dt><dd>{projectPartyValue(user, project)}</dd></div>
                  <div><dt>Start</dt><dd>{formatContractDate(project.planned_start_date) || "Nie ustawiono"}</dd></div>
                  <div><dt>Koniec</dt><dd>{formatContractDate(project.planned_end_date) || "Nie ustawiono"}</dd></div>
                  {!simpleMode && <div><dt>Ostatnia aktywność</dt><dd>{formatProjectActivityDate(project.updated_at || project.created_at) || "Nie ustawiono"}</dd></div>}
                  {!simpleMode && <div><dt>Kwota umowna</dt><dd>{contractAmountLabel(project) || "Nie podano"}</dd></div>}
                </dl>
                <div className="project-list-card__footer">
                  <div className="project-list-card__signals">
                    <span>{projectLastProgressLabel(project)}</span>
                    <span>{project.open_problem_count || 0} problemów</span>
                  </div>
                  <Button type="button" onClick={() => onProject(project)} variant="secondary">{isInvestor(user) ? "Edytuj" : "Otwórz"}</Button>
                </div>
              </article>
            ))}
          </div>
        )}
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

function AddProgressChoice({
  onPick,
  onClose,
}: {
  onPick: (entry: EntryModalState) => void;
  onClose: () => void;
}) {
  const options: Array<{
    icon: Parameters<typeof Icon>[0]["name"];
    title: string;
    subtitle: string;
    tone: string;
    entry: EntryModalState;
  }> = [
    { icon: "camera", title: "Zdjęcie", subtitle: "Dodaj zdjęcia", tone: "navy", entry: { kind: "update", mode: "photo" } },
    { icon: "mic", title: "Audio", subtitle: "Nagraj opis", tone: "navy", entry: { kind: "update", mode: "audio" } },
    { icon: "report", title: "Opis", subtitle: "Napisz co zrobiono", tone: "navy", entry: { kind: "update", mode: "text" } },
    { icon: "alert", title: "Problem", subtitle: "Zgłoś problem", tone: "red", entry: { kind: "problem", mode: "photo" } },
  ];

  return (
    <Modal title="Co chcesz dodać?" onClose={onClose}>
      <div className="progress-choice-grid">
        {options.map((option) => (
          <button
            type="button"
            className={`progress-choice progress-choice--${option.tone}`}
            onClick={() => onPick(option.entry)}
            key={option.title}
          >
            <span><Icon name={option.icon} size={34} /></span>
            <strong>{option.title}</strong>
            <small>{option.subtitle}</small>
          </button>
        ))}
      </div>
      <div className="progress-choice-footer">
        <Button type="button" variant="secondary" onClick={onClose}>Anuluj</Button>
      </div>
    </Modal>
  );
}

function WorkerMobileHeader({ title = "Majster firmy" }: { title?: string }) {
  return (
    <header className="worker-mobile-header">
      <Logo />
      <span><Icon name="clipboard" size={18} /> {title}</span>
    </header>
  );
}

function WorkerModeSwitch({
  uiMode,
  onUiModeChange,
}: {
  uiMode: UiMode;
  onUiModeChange: (mode: UiMode) => void;
}) {
  const simpleMode = uiMode === "simple";
  return (
    <div className="ui-mode-switch worker-mode-switch" role="group" aria-label="Przelacz tryb widoku">
      <button type="button" className={simpleMode ? "active" : ""} onClick={() => onUiModeChange("simple")}>Prosty</button>
      <button type="button" className={!simpleMode ? "active" : ""} onClick={() => onUiModeChange("advanced")}>Rozbudowany</button>
    </div>
  );
}

function CompanyWorkerProjectsPage({
  projects,
  onProject,
  uiMode,
  onUiModeChange,
  notify,
  onQueue,
  onChanged,
}: {
  projects: Project[];
  onProject: (project: Project) => void;
  uiMode: UiMode;
  onUiModeChange: (mode: UiMode) => void;
  notify: (toast: Toast) => void;
  onQueue: () => void;
  onChanged: () => void;
}) {
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortBy, setSortBy] = useState<"newest" | "oldest" | "end">("newest");
  const [choiceProject, setChoiceProject] = useState<Project | null>(null);
  const [entryModal, setEntryModal] = useState<{ project: Project; entry: EntryModalState } | null>(null);
  const simpleMode = uiMode === "simple";
  const statusOrder: Record<string, number> = { in_progress: 1, assigned: 2, completed: 3 };
  const visible = [...projects]
    .filter((project) => statusFilter === "all" || project.status === statusFilter)
    .sort((left, right) => {
      if (simpleMode) {
        const statusResult = (statusOrder[left.status] || 99) - (statusOrder[right.status] || 99);
        if (statusResult !== 0) return statusResult;
      }
      const value = (project: Project) => {
        if (sortBy === "end") return project.planned_end_date ? `${project.planned_end_date}T00:00:00` : project.updated_at || project.created_at;
        return project.updated_at || project.created_at;
      };
      const result = new Date(value(left)).getTime() - new Date(value(right)).getTime();
      return sortBy === "oldest" || sortBy === "end" ? result : -result;
    });

  function openEntry(project: Project, entry: EntryModalState) {
    setChoiceProject(null);
    setEntryModal({ project, entry });
  }

  return (
    <div className="page worker-home">
      <WorkerMobileHeader />
      <header className="worker-page-header">
        <div className="worker-title-row">
          <h1>Moje zlecenia</h1>
        </div>
        <WorkerModeSwitch uiMode={uiMode} onUiModeChange={onUiModeChange} />
      </header>

      {!simpleMode && (
        <section className="worker-filter-strip" aria-label="Filtry zlecen">
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="Status">
            <option value="all">Wszystkie statusy</option>
            <option value="assigned">Zlecone</option>
            <option value="in_progress">W realizacji</option>
            <option value="completed">Zakończone</option>
          </select>
          <select value={sortBy} onChange={(event) => setSortBy(event.target.value as typeof sortBy)} aria-label="Sortowanie">
            <option value="newest">Najnowsze</option>
            <option value="oldest">Najstarsze</option>
            <option value="end">Termin</option>
          </select>
        </section>
      )}

      {projects.length === 0 ? (
        <EmptyState icon="clipboard" title="Brak przypisanych zleceń" text="Gdy szef firmy przypisze Ci pracę, pojawi się tutaj." />
      ) : (
        <section className={`worker-project-list ${simpleMode ? "worker-project-list--simple" : "worker-project-list--advanced"}`}>
          {visible.map((project) => {
            const stage = projectStageLabel(project);
            const due = formatContractDate(project.planned_end_date || project.planned_start_date) || "Termin nieustawiony";
            return (
              <article className={`worker-job-card worker-job-card--${project.status}`} key={project.id}>
                <button type="button" className="worker-job-card__main" onClick={() => onProject(project)}>
                  <span className="worker-job-card__icon"><Icon name="clipboard" /></span>
                  <div>
                    <h2>{project.name}</h2>
                    <p>{project.client_name || "Bez klienta"} · {project.address || "Adres nieuzupełniony"}</p>
                  </div>
                  <Icon name="back" className="worker-job-card__chevron" />
                </button>
                <div className="worker-job-card__meta">
                  <span className={`status status--${project.status}`}>{statusLabels[project.status] || project.status}</span>
                  <span>{stage}</span>
                  <span><Icon name="clipboard" size={16} /> {due}</span>
                  {!simpleMode && <span>{project.entry_count || 0} wpisów</span>}
                  {!simpleMode && <span>{project.open_problem_count || 0} problemów</span>}
                </div>
                <div className="worker-job-card__actions">
                  {!simpleMode && <Button type="button" variant="secondary" onClick={() => onProject(project)}>Szczegóły</Button>}
                  {!simpleMode && project.status !== "completed" && (
                    <Button type="button" icon="plus" onClick={() => setChoiceProject(project)}>Dodaj postęp</Button>
                  )}
                </div>
              </article>
            );
          })}
        </section>
      )}

      {choiceProject && (
        <AddProgressChoice
          onClose={() => setChoiceProject(null)}
          onPick={(entry) => openEntry(choiceProject, entry)}
        />
      )}
      {entryModal && (
        <NewEntryModal
          project={entryModal.project}
          kind={entryModal.entry.kind}
          mode={entryModal.entry.mode}
          onClose={() => setEntryModal(null)}
          onSaved={() => {
            setEntryModal(null);
            onChanged();
            notify({ kind: "success", message: "Wpis zapisany" });
          }}
          onQueued={() => {
            setEntryModal(null);
            onQueue();
            notify({ kind: "info", message: "Wpis zapisany offline i czeka na wysłanie" });
          }}
        />
      )}
    </div>
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
  const [stageId, setStageId] = useState(defaultEntryStageId(project));
  const [files, setFiles] = useState<File[]>([]);
  const [recordingTarget, setRecordingTarget] = useState<EntryTextTarget | null>(null);
  const [speechInfo, setSpeechInfo] = useState<SpeechRecognitionInfo>({ target: null, state: "idle", message: "" });
  const [speechInterim, setSpeechInterim] = useState("");
  const [showVoiceNote, setShowVoiceNote] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const speechRecognition = useRef<SpeechRecognitionInstance | null>(null);
  const speechTarget = useRef<EntryTextTarget | null>(null);
  const speechBaseText = useRef("");
  const speechFinalText = useRef("");
  const speechManualEdit = useRef(false);
  const speechStopping = useRef(false);

  function speechRecognitionConstructor(): SpeechRecognitionConstructor | null {
    const speechWindow = window as Window & {
      SpeechRecognition?: SpeechRecognitionConstructor;
      webkitSpeechRecognition?: SpeechRecognitionConstructor;
    };
    return speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition || null;
  }

  function updateTargetText(target: EntryTextTarget, value: string) {
    if (target === "description") {
      setBody(value);
    } else {
      setVoiceNote(value);
    }
  }

  function composeTranscript(base: string, finalText: string, interimText: string, target: EntryTextTarget): string {
    const separator = target === "description" ? " " : "\n\n";
    return [base.trim(), finalText.trim(), interimText.trim()].filter(Boolean).join(separator);
  }

  function applySpeechText(target: EntryTextTarget, interimText = "") {
    if (speechManualEdit.current) return;
    updateTargetText(target, composeTranscript(speechBaseText.current, speechFinalText.current, interimText, target));
  }

  function stopLiveTranscription(options: { keepMessage?: boolean } = {}) {
    speechStopping.current = true;
    const activeRecognition = speechRecognition.current;
    speechRecognition.current = null;
    if (activeRecognition) {
      activeRecognition.onresult = null;
      activeRecognition.onerror = null;
      activeRecognition.onend = null;
      try {
        activeRecognition.stop();
      } catch {
        activeRecognition.abort?.();
      }
    }
    speechTarget.current = null;
    speechFinalText.current = "";
    speechBaseText.current = "";
    speechManualEdit.current = false;
    setSpeechInterim("");
    if (!options.keepMessage) {
      setSpeechInfo({ target: null, state: "idle", message: "" });
    }
    window.setTimeout(() => { speechStopping.current = false; }, 0);
  }

  function startLiveTranscription(target: EntryTextTarget) {
    stopLiveTranscription({ keepMessage: false });
    const Constructor = speechRecognitionConstructor();
    if (!Constructor) {
      setSpeechInfo({
        target,
        state: "unsupported",
        message: "Transkrypcja live beta nie jest dostępna w tej przeglądarce. Nagranie audio nadal zostanie zapisane.",
      });
      return;
    }

    const currentText = target === "description" ? body : voiceNote;
    speechTarget.current = target;
    speechBaseText.current = currentText;
    speechFinalText.current = "";
    speechManualEdit.current = false;
    setSpeechInterim("");

    try {
      const recognition = new Constructor();
      recognition.lang = "pl-PL";
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.onresult = (event) => {
        if (speechTarget.current !== target) return;
        let finalText = "";
        let interimText = "";
        for (let index = event.resultIndex; index < event.results.length; index += 1) {
          const result = event.results[index];
          const transcript = result[0]?.transcript?.trim();
          if (!transcript) continue;
          if (result.isFinal) {
            finalText = [finalText, transcript].filter(Boolean).join(" ");
          } else {
            interimText = [interimText, transcript].filter(Boolean).join(" ");
          }
        }
        if (finalText) {
          speechFinalText.current = [speechFinalText.current, finalText].filter(Boolean).join(" ");
        }
        setSpeechInterim(interimText);
        applySpeechText(target, interimText);
      };
      recognition.onerror = () => {
        if (speechStopping.current) return;
        setSpeechInfo({
          target,
          state: "error",
          message: "Transkrypcja live beta chwilowo nie działa. Nagranie audio nadal zostanie zapisane.",
        });
      };
      recognition.onend = () => {
        if (speechStopping.current) return;
        speechRecognition.current = null;
        setSpeechInterim("");
        setSpeechInfo({
          target,
          state: "error",
          message: "Transkrypcja live beta została przerwana. Nagranie audio nadal zostanie zapisane.",
        });
      };
      speechRecognition.current = recognition;
      recognition.start();
      setSpeechInfo({
        target,
        state: "listening",
        message: "Transkrypcja live beta: włączona. Słucham i zapisuję tekst...",
      });
    } catch {
      setSpeechInfo({
        target,
        state: "unsupported",
        message: "Transkrypcja live beta nie jest dostępna w tej przeglądarce. Nagranie audio nadal zostanie zapisane.",
      });
    }
  }

  function markManualTextEdit(target: EntryTextTarget) {
    if (speechTarget.current !== target) return;
    speechManualEdit.current = true;
    setSpeechInfo({
      target,
      state: "manual",
      message: "Tekst edytujesz ręcznie. Transkrypcja live nie będzie nadpisywać pola.",
    });
  }

  useEffect(() => () => {
    stopLiveTranscription();
    recorder.current?.stop();
  }, []);

  async function startRecording(target: EntryTextTarget) {
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
      };
      recorder.current = mediaRecorder;
      mediaRecorder.start();
      setRecordingTarget(target);
      startLiveTranscription(target);
    } catch {
      setError("Przeglądarka nie udostępniła mikrofonu. Opis możesz wpisać ręcznie.");
    }
  }

  function stopRecording() {
    recorder.current?.stop();
    setRecordingTarget(null);
    stopLiveTranscription({ keepMessage: false });
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

  function renderSpeechStatus(target: EntryTextTarget) {
    if (speechInfo.target !== target || speechInfo.state === "idle") return null;
    return (
      <div className={`live-transcription live-transcription--${speechInfo.state}`}>
        <strong>{speechInfo.message}</strong>
        {speechInfo.state === "listening" && <span>Tekst możesz poprawić przed zapisaniem.</span>}
        {speechInterim && speechTarget.current === target && !speechManualEdit.current && (
          <span>Roboczo: {speechInterim}</span>
        )}
      </div>
    );
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
            <strong>{recordingTarget === "description" ? "Nagrywanie opisu..." : "Nagraj opis prac"}</strong>
            <span>Powiedz krótko, co zostało zrobione. Transkrypcja live beta ruszy, jeśli przeglądarka ją wspiera; tekst możesz poprawić przed zapisem.</span>
          </div>
        </div>
        {renderSpeechStatus("description")}
        <label>{kind === "problem" ? "Opis problemu" : "Opis prac"}<textarea rows={5} value={body} onChange={(e) => { markManualTextEdit("description"); setBody(e.target.value); }} placeholder={kind === "problem" ? "Co się wydarzyło i czego potrzeba?" : "Wpisz opis albo nagraj go przyciskiem powyżej."} /></label>
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
                <strong>{recordingTarget === "note" ? "Nagrywanie notatki..." : "Nagraj dłuższą notatkę"}</strong>
                <span>Nagranie zostanie zapisane, a transkrypcja live beta pojawi się, jeśli przeglądarka ją wspiera.</span>
              </div>
            </div>
            {renderSpeechStatus("note")}
            <label>Tekst dłuższej notatki<textarea rows={4} value={voiceNote} onChange={(event) => { markManualTextEdit("note"); setVoiceNote(event.target.value); }} placeholder="Tutaj pojawi się transkrypcja dłuższej notatki." /></label>
          </>
        )}
        {files.length > 0 && <div className="file-chips">{files.map((file, index) => <span key={`${file.name}-${index}`}>{file.type.startsWith("audio/") ? "Nagranie" : "Zdjęcie"} {index + 1}<button type="button" onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}>×</button></span>)}</div>}
        {error && <p className="form-error">{error}</p>}
        <Button type="submit" busy={busy} icon={kind === "problem" ? "alert" : "check"}>{navigator.onLine ? "Zapisz wpis" : "Zapisz do wysłania"}</Button>
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

function reportTypeLabel(report: Report): string {
  if (report.report_type === "daily") return "Raport dzienny";
  if (report.report_type === "final") return "Raport końcowy";
  return report.title || "Raport";
}

function reportStatusLabel(report: Report): string {
  if (report.status === "ready" || report.status === "published") return "Gotowy";
  if (report.status === "generating") return "Generowanie...";
  if (report.status === "failed") return "Błąd";
  return "Szkic";
}

function reportDisplayDate(report: Report): string {
  const value = report.report_date || report.published_at || report.created_at;
  if (!value) return "Brak daty";
  return new Intl.DateTimeFormat("pl").format(new Date(value));
}

function reportPdfHref(report: Report, guestToken?: string): string {
  const url = report.pdf_url || "";
  if (!guestToken || !url) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}guest_token=${encodeURIComponent(guestToken)}`;
}

function GeneratedReportsPanel({
  projectId,
  reports,
  guestToken,
  onRefresh,
  notify,
}: {
  projectId: string;
  reports: Report[];
  guestToken?: string;
  onRefresh: () => Promise<void> | void;
  notify: (toast: Toast) => void;
}) {
  const [busyType, setBusyType] = useState<"daily" | "final" | null>(null);
  const [dailyDate, setDailyDate] = useState(() => new Date().toISOString().slice(0, 10));
  const generatedReports = reports.filter(
    (report) => ["daily", "final"].includes(report.report_type) && report.pdf_url,
  );

  async function generateReport(type: "daily" | "final") {
    setBusyType(type);
    try {
      await api<Report>(
        `/projects/${projectId}/reports`,
        {
          method: "POST",
          body: JSON.stringify(type === "daily" ? { type, date: dailyDate } : { type }),
        },
        guestToken,
      );
      notify({
        kind: "success",
        message: type === "daily" ? "Raport dzienny wygenerowany" : "Raport końcowy wygenerowany",
      });
      await onRefresh();
    } catch (reason) {
      notify({
        kind: "error",
        message: reason instanceof Error ? reason.message : "Nie udało się wygenerować raportu PDF",
      });
    } finally {
      setBusyType(null);
    }
  }

  return (
    <section className="project-pdf-panel panel">
      <div className="panel__header">
        <div>
          <h2>Raporty PDF</h2>
          <p>Generuj raporty bez automatycznego otwierania PDF-a. Gotowe pliki znajdziesz na liście poniżej.</p>
        </div>
      </div>
      <div className="project-pdf-panel__body">
        <div className="pdf-generate-grid">
          <article>
            <div>
              <h3>Raport dzienny</h3>
              <p>Wpisy i zdjęcia z wybranego dnia.</p>
            </div>
            <label>
              Data raportu
              <input type="date" value={dailyDate} onChange={(event) => setDailyDate(event.target.value)} />
            </label>
            <Button
              variant="secondary"
              icon="report"
              busy={busyType === "daily"}
              onClick={() => void generateReport("daily")}
            >
              Wygeneruj dzienny raport PDF
            </Button>
          </article>
          <article>
            <div>
              <h3>Raport końcowy</h3>
              <p>Pełne podsumowanie zlecenia i historii prac.</p>
            </div>
            <Button
              icon="report"
              busy={busyType === "final"}
              onClick={() => void generateReport("final")}
            >
              Wygeneruj końcowy raport PDF
            </Button>
          </article>
        </div>

        <div className="generated-report-list">
          <h3>Wygenerowane raporty</h3>
          {generatedReports.length === 0 ? (
            <p className="empty-note">Brak wygenerowanych raportów. Wygeneruj raport dzienny albo końcowy.</p>
          ) : (
            generatedReports.map((report) => {
              const pdfHref = reportPdfHref(report, guestToken);
              return (
                <article key={report.id}>
                  <span><Icon name="report" /></span>
                  <div>
                    <strong>{reportTypeLabel(report)}</strong>
                    <small>{reportDisplayDate(report)}</small>
                  </div>
                  <div>
                    <small>Wygenerował</small>
                    <b>{report.generated_by_label || report.generated_by?.name || report.generated_by?.email || "Nie podano"}</b>
                  </div>
                  <span className={`report-status report-status--${report.status}`}>{reportStatusLabel(report)}</span>
                  <div className="generated-report-actions">
                    {pdfHref && <a className="button button--secondary" href={pdfHref} target="_blank" rel="noreferrer">Otwórz</a>}
                    {pdfHref && <a className="button" href={pdfHref} download>Pobierz</a>}
                  </div>
                </article>
              );
            })
          )}
        </div>
      </div>
    </section>
  );
}

function ProjectStageControls({
  project,
  canChangeStage,
  busyStageId,
  onSetCurrent,
}: {
  project: Project;
  canChangeStage: boolean;
  busyStageId?: string;
  onSetCurrent: (stageId: string) => void;
}) {
  const stages = project.stages || [];
  if (!stages.length) {
    return <p className="form-note">Etapy nie są jeszcze skonfigurowane.</p>;
  }
  return (
    <div className="simple-stages">
      {stages.map((stage, index) => {
        const canSetCurrent = canChangeStage && stage.status !== "active";
        return (
          <article className={`simple-stage simple-stage--${stage.status}`} key={stage.id}>
            <span>{stage.status === "completed" ? "✓" : index + 1}</span>
            <div>
              <strong>{stage.title}</strong>
              <small>{stageStatusText(stage)}</small>
            </div>
            {canSetCurrent && (
              <button
                type="button"
                className="simple-stage__action"
                disabled={busyStageId === stage.id}
                onClick={() => onSetCurrent(stage.id)}
              >
                {busyStageId === stage.id ? "Ustawiam..." : "Ustaw jako aktualny"}
              </button>
            )}
          </article>
        );
      })}
    </div>
  );
}

function ProjectView({
  projectId,
  guestToken,
  user,
  uiMode,
  onUiModeChange,
  onBack,
  onUnavailable,
  notify,
  onQueue,
}: {
  projectId: string;
  guestToken?: string;
  user?: User;
  uiMode?: UiMode;
  onUiModeChange?: (mode: UiMode) => void;
  onBack: () => void;
  onUnavailable?: () => void;
  notify: (toast: Toast) => void;
  onQueue: () => void;
}) {
  const [project, setProject] = useState<Project | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [clientLink, setClientLink] = useState<ClientLink | null>(null);
  const [loading, setLoading] = useState(true);
  const [entryModal, setEntryModal] = useState<EntryModalState | null>(null);
  const [showAddProgressChoice, setShowAddProgressChoice] = useState(false);
  const [showReports, setShowReports] = useState(false);
  const [showManage, setShowManage] = useState(false);
  const [showClientLink, setShowClientLink] = useState(false);
  const [showWorkerStagePicker, setShowWorkerStagePicker] = useState(false);
  const [busyStageId, setBusyStageId] = useState<string | undefined>();
  const fieldMode = Boolean(guestToken) || uiMode !== "advanced";

  const load = useCallback(async () => {
    try {
      const [projectData, entryData] = await Promise.all([
        api<Project>(`/projects/${projectId}`, {}, guestToken),
        api<Entry[]>(`/projects/${projectId}/entries`, {}, guestToken),
      ]);
      setProject(projectData);
      setEntries(entryData);
      const canLoadReports = guestToken
        ? projectData.guest && ["history", "view"].includes(projectData.guest.permission)
        : true;
      if (canLoadReports) {
        const reportData = await api<Report[]>(`/projects/${projectId}/reports`, {}, guestToken);
        setReports(reportData);
      } else {
        setReports([]);
      }
      if (!guestToken) {
        const linkData = await api<ClientLink>(`/projects/${projectId}/client-link`);
        setClientLink(linkData);
      }
    } catch (reason) {
      setProject(null);
      setEntries([]);
      setReports([]);
      setClientLink(null);
      if (!guestToken && reason instanceof ApiError && [403, 404].includes(reason.status)) {
        notify({ kind: "info", message: "Nie masz dostępu do tego zlecenia w tej sesji. Wracam do listy." });
        onUnavailable?.();
        return;
      }
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się otworzyć projektu" });
    } finally {
      setLoading(false);
    }
  }, [guestToken, notify, onUnavailable, projectId]);

  useEffect(() => {
    setProject(null);
    setEntries([]);
    setReports([]);
    setClientLink(null);
    setShowReports(false);
    setShowManage(false);
    setShowClientLink(false);
    setShowAddProgressChoice(false);
    setShowWorkerStagePicker(false);
    setLoading(true);
  }, [guestToken, projectId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!reports.some((report) => report.status === "generating")) return;
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [load, reports]);

  if (loading) return <div className="page"><div className="loading-screen"><span className="spinner" /> Ładowanie projektu...</div></div>;
  if (!project) return <div className="page"><EmptyState icon="alert" title="Nie udało się otworzyć projektu" text="Link może być nieaktywny albo nie masz dostępu." /></div>;

  const canAdd = !project.guest || ["add", "history"].includes(project.guest.permission);
  const { completedCount, progress } = projectStageProgress(project);
  const canGeneratePdfReports = Boolean(
    guestToken
      ? project.guest && ["history", "view"].includes(project.guest.permission)
      : user,
  );
  const canReopenProject = Boolean(user && !guestToken && ["owner", "manager"].includes(project.role || "") && !isCompanyWorker(user));
  const canCloseProject = Boolean(
    user
    && !guestToken
    && project.status !== "completed"
    && (["owner", "manager"].includes(project.role || "") || isCompanyWorker(user)),
  );
  const hasProjectStages = Boolean(project.stages?.length);
  const canChangeStage = Boolean(
    hasProjectStages
    && project.status !== "completed"
    && (user && isCompanyWorker(user) && !guestToken ? true : canAdd),
  );
  const projectIdForStatusActions = project.id;

  function changeMode(next: "field" | "expanded") {
    onUiModeChange?.(next === "field" ? "simple" : "advanced");
  }

  async function copyClientLink() {
    if (!clientLink?.url) return;
    setShowClientLink(true);
    const copied = await copyToClipboard(clientLink.url);
    notify({ kind: copied ? "success" : "info", message: copied ? "Stały link klienta został skopiowany." : "Stały link klienta jest poniżej. Skopiuj go ręcznie." });
  }

  async function closeProject() {
    if (!window.confirm("Czy na pewno chcesz zamknąć zlecenie? Status zmieni się na Zakończono.")) return;
    try {
      await api(`/projects/${projectIdForStatusActions}/close`, { method: "POST", body: JSON.stringify({}) });
      await load();
      notify({ kind: "success", message: "Zlecenie zostało zamknięte." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zamknąć zlecenia" });
    }
  }

  async function reopenProject() {
    if (!window.confirm("Czy chcesz ponownie otworzyć zlecenie? Status wróci do W realizacji.")) return;
    try {
      await api(`/projects/${projectIdForStatusActions}/reopen`, { method: "POST", body: JSON.stringify({}) });
      await load();
      notify({ kind: "success", message: "Zlecenie wróciło do realizacji." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się otworzyć zlecenia ponownie" });
    }
  }

  async function setCurrentStage(stageId: string) {
    setBusyStageId(stageId);
    try {
      await api(`/projects/${projectIdForStatusActions}/stages/${stageId}/set-current`, {
        method: "POST",
        body: JSON.stringify({}),
      }, guestToken);
      await load();
      notify({ kind: "success", message: "Etap zlecenia zaktualizowany." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zmienić etapu" });
    } finally {
      setBusyStageId(undefined);
    }
  }

  const stages = (
    <ProjectStageControls
      project={project}
      canChangeStage={canChangeStage}
      busyStageId={busyStageId}
      onSetCurrent={(stageId) => void setCurrentStage(stageId)}
    />
  );
  const currentStage = activeProjectStage(project);
  const currentStageIndex = currentStage && project.stages ? project.stages.findIndex((stage) => stage.id === currentStage.id) + 1 : 0;
  const recentEntries = entries.slice(0, 3);
  const canAddWorkerProgress = canAdd && project.status !== "completed";

  if (user && isCompanyWorker(user) && !guestToken) {
    return (
      <div className="worker-workspace">
        <header className="worker-detail-hero">
          <div className="worker-detail-topbar">
            <button type="button" className="worker-back-button" onClick={onBack}><Icon name="back" /> Wróć do zleceń</button>
            <span><Icon name="clipboard" size={17} /> Majster firmy</span>
          </div>
          <div className="worker-detail-hero__main">
            <span className="worker-detail-hero__icon"><Icon name="clipboard" /></span>
            <div>
              <h1>{project.name}</h1>
              <p>{project.client_name || "Bez klienta"} · {project.address || "Adres nieuzupełniony"}</p>
            </div>
            <span className={`status status--${project.status}`}>{statusLabels[project.status] || project.status}</span>
          </div>
          <div className="worker-detail-mode">
            <WorkerModeSwitch uiMode={uiMode || "simple"} onUiModeChange={(mode) => onUiModeChange?.(mode)} />
          </div>
        </header>

        <main className="worker-detail-main">
          <section className="worker-detail-card worker-stage-card">
            <span className="worker-stage-card__number">{currentStageIndex || "–"}</span>
            <div>
              <small>Aktualny etap</small>
              <h2>{currentStage?.title || "Etap nieustawiony"}</h2>
              <p>{currentStage ? stageStatusText(currentStage) : "Szef firmy nie ustawił jeszcze etapu."}</p>
            </div>
            {canChangeStage && (
              <button type="button" className="worker-stage-change" onClick={() => setShowWorkerStagePicker((current) => !current)}>
                Zmień etap
              </button>
            )}
            {showWorkerStagePicker && (
              <div className="worker-stage-picker">
                {stages}
              </div>
            )}
          </section>

          <section className="worker-detail-card worker-terms-card">
            <div>
              <small>Planowany start</small>
              <strong>{formatContractDate(project.planned_start_date) || "Nie ustawiono"}</strong>
            </div>
            <div>
              <small>Planowany koniec</small>
              <strong>{formatContractDate(project.planned_end_date) || "Nie ustawiono"}</strong>
            </div>
            <div>
              <small>Ostatnia aktywność</small>
              <strong>{formatProjectActivityDate(project.updated_at || project.created_at) || "Brak daty"}</strong>
            </div>
            {contractAmountLabel(project) && (
              <div>
                <small>Kwota umowna</small>
                <strong>{contractAmountLabel(project)}</strong>
              </div>
            )}
          </section>

          <section className="worker-detail-card">
            <div className="worker-section-heading">
              <div>
                <h2>Ostatnie dodane</h2>
                <p>Najświeższe wpisy z tej realizacji.</p>
              </div>
            </div>
            {recentEntries.length === 0 ? (
              <div className="worker-empty-history">
                <Icon name="camera" />
                <strong>Tu powstanie historia pracy</strong>
                <p>Dodaj pierwszy postęp: zdjęcia, opis, audio albo problem.</p>
              </div>
            ) : (
              <div className="worker-entry-list">
                {recentEntries.map((entry) => (
                  <article key={entry.id}>
                    <span><Icon name={entry.kind === "problem" ? "alert" : entry.media.some((asset) => asset.kind === "audio") ? "mic" : "camera"} /></span>
                    <div>
                      <strong>{entry.kind === "problem" ? "Problem" : entry.media.length ? "Dokumentacja" : "Opis"}</strong>
                      <small>{new Intl.DateTimeFormat("pl", { dateStyle: "short", timeStyle: "short" }).format(new Date(entry.occurred_at))}</small>
                    </div>
                    <Icon name="back" className="worker-entry-list__arrow" />
                  </article>
                ))}
              </div>
            )}
          </section>

          {(canAddWorkerProgress || canCloseProject || canReopenProject || project.status === "completed") && (
            <section className="worker-action-panel">
              {canAddWorkerProgress && <Button type="button" icon="plus" onClick={() => setShowAddProgressChoice(true)}>Dodaj postęp</Button>}
              {canCloseProject ? (
                <Button type="button" variant="secondary" className="worker-finish-button" onClick={closeProject}>Zakończ robotę</Button>
              ) : canReopenProject && project.status === "completed" ? (
                <Button type="button" variant="secondary" onClick={reopenProject}>Otwórz ponownie</Button>
              ) : project.status === "completed" ? (
                <div className="worker-completed-note"><Icon name="check" /> Zlecenie zakończone</div>
              ) : null}
            </section>
          )}

          {uiMode === "advanced" && (
            <>
              <section className="worker-detail-card worker-advanced-stage-summary">
                <div className="worker-section-heading">
                  <div><h2>Etapy pracy</h2><p>Widok informacyjny i zmiana etapu, jeśli obecne uprawnienia na to pozwalają.</p></div>
                </div>
                {stages}
              </section>
              {canGeneratePdfReports && reports.length > 0 && (
                <section className="worker-detail-card worker-report-list">
                  <div className="worker-section-heading">
                    <div><h2>Raporty PDF</h2><p>Gotowe raporty do podglądu.</p></div>
                  </div>
                  {reports.map((report) => {
                    const href = reportPdfHref(report, guestToken);
                    return (
                      <article key={report.id}>
                        <span><Icon name="report" /></span>
                        <div>
                          <strong>{reportTypeLabel(report)}</strong>
                          <small>{reportDisplayDate(report)}</small>
                        </div>
                        {href && <a className="button button--secondary" href={href} target="_blank" rel="noreferrer">Otwórz</a>}
                      </article>
                    );
                  })}
                </section>
              )}
            </>
          )}
        </main>

        {showAddProgressChoice && (
          <AddProgressChoice
            onClose={() => setShowAddProgressChoice(false)}
            onPick={(entry) => {
              setShowAddProgressChoice(false);
              setEntryModal(entry);
            }}
          />
        )}
        {entryModal && <NewEntryModal project={project} kind={entryModal.kind} mode={entryModal.mode} guestToken={guestToken} onClose={() => setEntryModal(null)} onSaved={() => { setEntryModal(null); load(); notify({ kind: "success", message: "Wpis zapisany" }); }} onQueued={() => { setEntryModal(null); onQueue(); notify({ kind: "info", message: "Wpis zapisany offline i czeka na wysłanie" }); }} />}
      </div>
    );
  }

  if (fieldMode) {
    return (
      <div className="field-mode">
        <header className="field-mode__header">
          <button onClick={onBack}><Icon name="back" /></button>
          <div className="field-brand"><img src="/brand/app-icon.png" alt="" /><strong>Pan Majster</strong></div>
          <span />
        </header>
        <main>
          <section className="field-project">
            <small>ZLECENIE</small>
            <h1>{project.name}</h1>
            <p>{project.address}</p>
            <ContractTermsPanel project={project} />
            <span className={`status status--${project.status}`}>● {statusLabels[project.status]}</span>
            {canCloseProject && <Button variant="danger" onClick={closeProject}>Zamknij zlecenie</Button>}
            {canReopenProject && project.status === "completed" && <Button variant="success" onClick={reopenProject}>Otwórz ponownie</Button>}
            {!guestToken && (
              <div className="field-mode-switcher">
                <p>Tryb prosty: najważniejsze akcje do pracy w terenie.</p>
                <div className="ui-mode-switch ui-mode-switch--field" role="group" aria-label="Przełącz tryb widoku zlecenia">
                  <button type="button" className="active" onClick={() => changeMode("field")}>Prosty</button>
                  <button type="button" onClick={() => changeMode("expanded")}>Rozbudowany</button>
                </div>
              </div>
            )}
          </section>
          {!navigator.onLine && <div className="offline-banner"><Icon name="sync" /> Tryb offline — wpis zostanie wysłany po odzyskaniu sieci.</div>}
          {canAdd && <div className="field-actions">
            <FieldAction icon="camera" title="Zdjęcie" subtitle="Dodaj postęp z placu" tone="navy" onClick={() => setEntryModal({ kind: "update", mode: "photo" })} />
            <FieldAction icon="mic" title="Audio" subtitle="Nagraj opis pracy" tone="orange" onClick={() => setEntryModal({ kind: "update", mode: "audio" })} />
            <FieldAction icon="send" title="Opis" subtitle="Krótka notatka tekstowa" tone="navy" onClick={() => setEntryModal({ kind: "update", mode: "text" })} />
            <FieldAction icon="alert" title="Problem" subtitle="Usterka lub decyzja" tone="red" onClick={() => setEntryModal({ kind: "problem", mode: "photo" })} />
          </div>}
          {canGeneratePdfReports && (
            <GeneratedReportsPanel
              projectId={projectIdForStatusActions}
              reports={reports}
              guestToken={guestToken}
              onRefresh={load}
              notify={notify}
            />
          )}
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
            {!guestToken && user?.profile_type !== "investor" && !isCompanyWorker(user) && clientLink && <Button variant="secondary" icon="link" onClick={copyClientLink}>Link klienta</Button>}
            {canCloseProject && <Button variant="danger" onClick={closeProject}>Zamknij zlecenie</Button>}
            {canReopenProject && project.status === "completed" && <Button variant="success" onClick={reopenProject}>Otwórz ponownie</Button>}
            {!guestToken && project.can_edit_details && <Button variant="secondary" icon="settings" onClick={() => setShowManage(true)}>Edytuj zlecenie</Button>}
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
          <div className="progress-value"><strong>{progress}%</strong><span>{completedCount} z {project.stages?.length || 0} etapów ukończonych</span></div>
          <div className="progress"><i style={{ width: `${progress}%` }} /></div>
          <ContractTermsPanel project={project} />
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
      {canGeneratePdfReports && (
        <GeneratedReportsPanel
          projectId={projectIdForStatusActions}
          reports={reports}
          guestToken={guestToken}
          onRefresh={load}
          notify={notify}
        />
      )}
      {entryModal && <NewEntryModal project={project} kind={entryModal.kind} mode={entryModal.mode} guestToken={guestToken} onClose={() => setEntryModal(null)} onSaved={() => { setEntryModal(null); load(); notify({ kind: "success", message: "Wpis zapisany" }); }} onQueued={() => { setEntryModal(null); onQueue(); notify({ kind: "info", message: "Wpis zapisany offline" }); }} />}
      {showReports && <ReportModal project={project} reports={reports} onClose={() => setShowReports(false)} onRefresh={load} notify={notify} />}
      {showManage && <ManageProjectModal project={project} user={user} onClose={() => setShowManage(false)} onRefresh={load} notify={notify} />}
    </div>
  );
}

function ReportsPage({ user, projects, onOpen }: { user: User; projects: Project[]; onOpen: (project: Project) => void }) {
  const [tab, setTab] = useState<"all" | "open" | "history">("all");
  const [query, setQuery] = useState("");
  const [sortBy, setSortBy] = useState<"issued" | "ended">("issued");
  const [sortDirection, setSortDirection] = useState<"newest" | "oldest">("newest");
  const [collapsedReports, setCollapsedReports] = useState<string[]>([]);

  function reportMaterialCount(project: Project): number {
    return project.entry_count || 0;
  }

  function reportSortDate(project: Project): number {
    const value = sortBy === "ended"
      ? project.planned_end_date
        ? `${project.planned_end_date}T00:00:00`
        : project.updated_at || project.created_at
      : project.updated_at || project.created_at;
    const timestamp = new Date(value).getTime();
    return Number.isFinite(timestamp) ? timestamp : 0;
  }

  function reportBadge(project: Project): string {
    const count = reportMaterialCount(project);
    if (count === 0) return "0 wpisów";
    if (count === 1) return "1 wpis";
    return `${count} wpisów`;
  }

  function toggleReportDetails(projectId: string) {
    setCollapsedReports((current) => current.includes(projectId)
      ? current.filter((item) => item !== projectId)
      : [...current, projectId]);
  }

  const openProjects = projects.filter((project) => project.status !== "completed");
  const historicalProjects = projects.filter((project) => project.status === "completed");
  const openReportMaterial = openProjects.reduce((sum, project) => sum + reportMaterialCount(project), 0);
  const historicalReportMaterial = historicalProjects.reduce((sum, project) => sum + reportMaterialCount(project), 0);
  const multiReportProjects = projects.filter((project) => reportMaterialCount(project) > 1).length;
  const queryText = query.trim().toLowerCase();
  const source = tab === "all" ? projects : tab === "open" ? openProjects : historicalProjects;
  const visibleProjects = [...source]
    .filter((project) =>
      `${project.name} ${project.client_name || ""} ${project.address || ""} ${project.worker_profile?.label || ""}`
        .toLowerCase()
        .includes(queryText),
    )
    .sort((left, right) => {
      const result = reportSortDate(left) - reportSortDate(right);
      return sortDirection === "newest" ? -result : result;
    });

  return (
    <div className="page reports-page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Dokumentacja</span>
          <h1>Raporty</h1>
          <p>Przeglądaj projekty raportowe i otwieraj raporty tworzone w ramach zleceń.</p>
        </div>
      </header>

      <div className="report-tabs" role="tablist" aria-label="Widok raportów">
        <button type="button" className={tab === "all" ? "active" : ""} onClick={() => setTab("all")}>Wszystkie</button>
        <button type="button" className={tab === "open" ? "active" : ""} onClick={() => setTab("open")}>Otwarte</button>
        <button type="button" className={tab === "history" ? "active" : ""} onClick={() => setTab("history")}>Historyczne</button>
      </div>

      <section className="panel report-metrics">
        <article>
          <span><Icon name="report" /></span>
          <div><small>Otwarte raporty / wpisy</small><strong>{openReportMaterial}</strong><p>{openProjects.length} zleceń otwartych</p></div>
        </article>
        <article>
          <span className="metric-green"><Icon name="sync" /></span>
          <div><small>Historyczne raporty / wpisy</small><strong>{historicalReportMaterial}</strong><p>{historicalProjects.length} zleceń zakończonych</p></div>
        </article>
        <article>
          <span className="metric-orange"><Icon name="clipboard" /></span>
          <div><small>Zlecenia z wieloma wpisami</small><strong>{multiReportProjects}</strong><p>na podstawie wpisów postępu</p></div>
        </article>
      </section>

      <section className="report-toolbar">
        <input
          type="search"
          placeholder="Szukaj po nazwie zlecenia, kliencie lub wykonawcy..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="report-sort-controls" aria-label="Sortowanie raportów">
          <button type="button" className={sortBy === "issued" ? "active" : ""} onClick={() => setSortBy("issued")}>Data wystawienia</button>
          <button type="button" className={sortBy === "ended" ? "active" : ""} onClick={() => setSortBy("ended")}>Data zakończenia zlecenia</button>
          <button type="button" className={sortDirection === "newest" ? "active" : ""} onClick={() => setSortDirection("newest")}>Najnowsze</button>
          <button type="button" className={sortDirection === "oldest" ? "active" : ""} onClick={() => setSortDirection("oldest")}>Najstarsze</button>
        </div>
      </section>

      <section className="report-project-list">
        {visibleProjects.length === 0 ? (
          <EmptyState icon="report" title="Brak raportów w tym widoku" text="Zmień zakładkę albo frazę wyszukiwania." />
        ) : visibleProjects.map((project) => {
          const count = reportMaterialCount(project);
          const isExpanded = count > 0 && !collapsedReports.includes(project.id);
          return (
            <article className="report-project-card panel" key={project.id}>
              <header>
                <span className="report-project-card__icon"><Icon name="report" /></span>
                <div className="report-project-card__main">
                  <h2>{project.name}</h2>
                  <p>{project.address || "Adres nieuzupełniony"}{project.client_name ? ` · Klient: ${project.client_name}` : ""}</p>
                </div>
                <span className={`status status--${project.status}`}>{statusLabels[project.status] || project.status}</span>
                <dl>
                  <div><dt>{projectPartyLabel(user)}</dt><dd>{projectPartyValue(user, project)}</dd></div>
                  <div><dt>Planowane zakończenie</dt><dd>{formatContractDate(project.planned_end_date) || "Nie ustawiono"}</dd></div>
                </dl>
                <button type="button" className="report-count-badge" onClick={() => toggleReportDetails(project.id)} aria-expanded={isExpanded}>
                  {reportBadge(project)}
                  {count > 0 && <Icon name="back" size={15} />}
                </button>
              </header>
              <section className={`report-materials ${count === 0 ? "report-materials--empty" : ""}`}>
                <div className="report-materials__heading">
                  <span>Raporty do tego zlecenia</span>
                  <small>{count === 0 ? "Brak gotowych materiałów" : reportBadge(project)}</small>
                </div>
                {count === 0 ? (
                  <div className="report-empty-line">
                    <Icon name="report" size={18} />
                    <span>Brak wpisów postępu do raportu.</span>
                  </div>
                ) : isExpanded ? (
                  <div className="report-sublist">
                    <article>
                      <span><Icon name="report" /></span>
                      <div>
                        <span className="report-row-label">Raport</span>
                        <strong>Raport zlecenia</strong>
                        <small>{count > 0 ? `Materiał raportowy z ${count} ${count === 1 ? "wpisu" : "wpisów"}.` : "Brak wpisów postępu do raportu."}</small>
                      </div>
                      <div><small>Data wystawienia</small><strong>{new Intl.DateTimeFormat("pl").format(new Date(project.updated_at || project.created_at))}</strong></div>
                      <div><small>Etap / Status</small><span className={`status status--${project.status}`}>{statusLabels[project.status] || project.status}</span></div>
                      <Button type="button" variant="secondary" icon="report" onClick={() => onOpen(project)}>Otwórz raport</Button>
                    </article>
                  </div>
                ) : (
                  <div className="report-empty-line report-empty-line--collapsed">
                    <Icon name="report" size={18} />
                    <span>Raporty ukryte. Rozwiń licznik wpisów, aby je pokazać.</span>
                  </div>
                )}
              </section>
            </article>
          );
        })}
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
  const [workerSearch, setWorkerSearch] = useState("");
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
    const isPersonal = workspace?.kind === "personal";
    const warning = worker.assigned_projects.length > 0
      ? isPersonal
        ? "Ten wykonawca ma przypisane inwestycje. Dezaktywacja odepnie wykonawcę od tych inwestycji. Kontynuować?"
        : "Ten majster/ekipa ma przypisane zlecenia. Dezaktywacja odepnie wykonawcę od tych zleceń. Kontynuować?"
      : isPersonal ? "Dezaktywować tego wykonawcę?" : "Dezaktywować tego majstra/ekipę?";
    if (!window.confirm(warning)) return;
    setBusy(true);
    try {
      await api(`/workers/${worker.id}`, { method: "DELETE" });
      load();
      onChanged();
      notify({ kind: "success", message: isPersonal ? "Wykonawca został dezaktywowany." : "Majster/ekipa została dezaktywowana." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : isPersonal ? "Nie udało się dezaktywować wykonawcy" : "Nie udało się dezaktywować majstra/ekipy" });
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
      notify({ kind: "success", message: workspace?.kind === "personal" ? "Wykonawca został aktywowany ponownie." : "Majster/ekipa została aktywowana ponownie." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : workspace?.kind === "personal" ? "Nie udało się aktywować wykonawcy" : "Nie udało się aktywować majstra/ekipy" });
    } finally {
      setBusy(false);
    }
  }

  const workerQuery = workerSearch.trim().toLowerCase();
  const filteredWorkerProfiles = (workspace?.worker_profiles || []).filter((worker) =>
    `${worker.label} ${worker.email || ""} ${worker.phone || ""} ${worker.note || ""}`
      .toLowerCase()
      .includes(workerQuery),
  );

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
                <h4>{workspace.kind === "personal" ? "Wykonawcy" : "Majstrowie i ekipy"}</h4>
                <div className="directory-toolbar directory-toolbar--compact">
                  <input
                    type="search"
                    value={workerSearch}
                    onChange={(event) => setWorkerSearch(event.target.value)}
                    placeholder={workspace.kind === "personal" ? "Szukaj wykonawcy po nazwie..." : "Szukaj majstra lub ekipy..."}
                  />
                </div>
                {filteredWorkerProfiles.length === 0 ? <p className="empty-note">Brak wyników dla tej frazy.</p> : filteredWorkerProfiles.map((worker) => (
                  <article className={`clickable-card ${!worker.active ? "is-muted" : ""}`} key={worker.id}>
                    <span>{worker.label.slice(0, 2).toUpperCase()}</span>
                    <div>
                      <strong>{workspace.kind === "personal" ? "Wykonawca" : workerKindLabel(worker)}: {worker.label}</strong>
                      <small>
                        {worker.email || "Bez e-maila"} · {worker.account_type === "account" ? "konto po potwierdzeniu e-mail" : "link-only"} · {worker.assigned_projects.length} {workspace.kind === "personal" ? "inwestycji" : "zleceń"}
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
              <label>{workspace.kind === "personal" ? "Nazwa wykonawcy" : "Nazwa majstra / ekipy"}<input name="label" required placeholder={workspace.kind === "personal" ? "np. Firma remontowa albo hydraulik" : "np. Mieciu hydraulik"} /></label>
              <div className="form-row">
                <label>E-mail opcjonalnie<input type="email" name="email" placeholder="Możesz zostawić puste" /></label>
                <label>Telefon opcjonalnie<input name="phone" /></label>
              </div>
              <label>{workspace.kind === "personal" ? "Profesja / specjalizacja wykonawcy" : "Profesja / specjalizacja majstra lub ekipy"}<textarea name="note" rows={2} placeholder="np. hydraulik, ogrodnik, glazurnik" /></label>
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
            <label>{workspace?.kind === "personal" ? "Profesja / specjalizacja wykonawcy" : "Profesja / specjalizacja majstra lub ekipy"}<textarea name="note" rows={3} defaultValue={editingWorker.note} placeholder="np. hydraulik, ogrodnik, glazurnik" /></label>
            <Button type="submit" busy={busy}>Zapisz wykonawcę</Button>
          </form>
        </Modal>
      )}
    </Modal>
  );
}

function InvestorContractorsPanel({
  workspaceId,
  projects,
  onOpenProject,
  onChanged,
  notify,
}: {
  workspaceId: string;
  projects: Project[];
  onOpenProject: (project: Project) => void;
  onChanged: () => void;
  notify: (toast: Toast) => void;
}) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [invitationUrl, setInvitationUrl] = useState("");
  const [editingWorker, setEditingWorker] = useState<WorkerProfile | null>(null);
  const [previewWorker, setPreviewWorker] = useState<WorkerProfile | null>(null);
  const [showAddContractor, setShowAddContractor] = useState(false);
  const [contractorSearch, setContractorSearch] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<Workspace>(`/workspaces/${workspaceId}`).then(setWorkspace).catch((reason) => {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się otworzyć wykonawców" });
    });
  }, [notify, workspaceId]);

  useEffect(() => { load(); }, [load]);

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
      setShowAddContractor(false);
      load();
      onChanged();
      notify({ kind: worker.existing ? "info" : "success", message: worker.message || "Wykonawca dodany." });
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
      ? "Ten wykonawca ma przypisane inwestycje. Dezaktywacja odepnie wykonawcę od tych inwestycji. Kontynuować?"
      : "Dezaktywować tego wykonawcę?";
    if (!window.confirm(warning)) return;
    setBusy(true);
    try {
      await api(`/workers/${worker.id}`, { method: "DELETE" });
      load();
      onChanged();
      notify({ kind: "success", message: "Wykonawca został dezaktywowany." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się dezaktywować wykonawcy" });
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
      notify({ kind: "success", message: "Wykonawca został aktywowany ponownie." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się aktywować wykonawcy" });
    } finally {
      setBusy(false);
    }
  }

  if (!workspace) {
    return <section className="panel"><div className="loading-screen"><span className="spinner" /> Ładowanie wykonawców...</div></section>;
  }

  const workers = workspace.worker_profiles || [];
  const workerLinks = workspace.worker_links || [];
  const query = contractorSearch.trim().toLowerCase();
  const filteredWorkers = workers.filter((worker) =>
    `${worker.label} ${worker.email || ""} ${worker.phone || ""} ${worker.note || ""} ${worker.assigned_projects.map((project) => project.name).join(" ")}`
      .toLowerCase()
      .includes(query),
  );
  const activeWorkers = workers.filter((worker) => worker.active);
  const inactiveWorkers = workers.filter((worker) => !worker.active);
  const previewProjects = previewWorker
    ? projects.filter((project) => project.worker_profile_id === previewWorker.id || project.worker_profile?.id === previewWorker.id)
    : [];
  const previewActiveProjects = previewProjects.filter((project) => ["assigned", "in_progress"].includes(project.status));
  const previewHistoryProjects = previewProjects.filter((project) => project.status === "completed");
  const previewOtherProjects = previewProjects.filter((project) => !["assigned", "in_progress", "completed"].includes(project.status));
  const previewAssignedOnly = previewWorker
    ? previewWorker.assigned_projects.filter((assigned) => !previewProjects.some((project) => project.id === assigned.id))
    : [];

  function openPreviewProject(project: Project) {
    setPreviewWorker(null);
    onOpenProject(project);
  }

  return (
    <>
      <section className="panel company-team-panel investor-contractors-panel">
        <div className="company-team-header">
          <div>
            <span className="eyebrow">Typ konta: Inwestor</span>
            <h2>Wykonawcy</h2>
            <p>Zarządzaj wykonawcami, których przypisujesz do inwestycji.</p>
          </div>
          <div className="company-team-meta">
            <span>{activeWorkers.length} aktywnych</span>
            <span>{inactiveWorkers.length} nieaktywnych</span>
            <Button type="button" icon="plus" onClick={() => setShowAddContractor(true)}>Dodaj wykonawcę</Button>
          </div>
        </div>
        {invitationUrl && <div className="share-result share-result--compact"><input value={invitationUrl} readOnly /><Button variant="secondary" onClick={() => void copyToClipboard(invitationUrl)}>Kopiuj link</Button></div>}
      </section>

      <section className="panel directory-panel team-directory-panel investor-contractors-list">
        <div className="panel__header">
          <div>
            <h2>Lista wykonawców</h2>
            <p>{workers.length === 1 ? "1 wykonawca" : `${workers.length} wykonawców`} w tej liście.</p>
          </div>
        </div>
        <div className="directory-toolbar">
          <input
            type="search"
            value={contractorSearch}
            onChange={(event) => setContractorSearch(event.target.value)}
            placeholder="Szukaj wykonawcy po nazwie"
          />
        </div>
        <div className="contractor-table-head" aria-hidden="true">
          <span>Wykonawca</span>
          <span>Status</span>
          <span>Inwestycje</span>
          <span>Akcje</span>
        </div>
        <div className="contractor-list">
          {workers.length === 0 ? (
            <EmptyState icon="users" title="Brak wykonawców" text="Dodaj pierwszego wykonawcę, żeby później przypisać go do inwestycji.">
            </EmptyState>
          ) : filteredWorkers.length === 0 ? (
            <p className="empty-note">Brak wyników dla tej frazy.</p>
          ) : filteredWorkers.map((worker) => {
            const assignedCount = worker.assigned_projects.length;
            return (
              <article className={`contractor-card ${!worker.active ? "is-muted" : ""}`} key={worker.id}>
                <div className={`contractor-card__avatar ${worker.profile_kind === "crew" ? "contractor-card__avatar--crew" : ""}`}>
                  {worker.label.slice(0, 2).toUpperCase()}
                </div>
                <div className="contractor-card__main">
                  <strong>{worker.label}</strong>
                  <small>{worker.profile_kind === "crew" ? "Firma / ekipa zewnętrzna" : "Wykonawca"}</small>
                  <small>{worker.email || "Bez e-maila"}{worker.phone ? ` · ${worker.phone}` : " · Telefon nie podano"}</small>
                  {worker.note && <small>{worker.note}</small>}
                </div>
                <div className="contractor-card__status">
                  <span className={`status ${worker.active ? "status--active" : "status--archived"}`}>{worker.active ? "Aktywny" : "Nieaktywny"}</span>
                  <small>{worker.account_type === "account" ? "konto po potwierdzeniu e-mail" : "link-only"}</small>
                </div>
                <div className="contractor-card__assignments">
                  <strong>{assignedCount}</strong>
                  <small>{assignedCount === 1 ? "inwestycja" : "inwestycji"}</small>
                  {assignedCount > 0 ? (
                    <span>{worker.assigned_projects.slice(0, 2).map((project) => project.name).join(", ")}{assignedCount > 2 ? ` +${assignedCount - 2}` : ""}</span>
                  ) : (
                    <span>Brak przypisań</span>
                  )}
                </div>
                <div className="contractor-card__actions">
                  <Button type="button" variant="secondary" onClick={() => setPreviewWorker(worker)}>Podgląd zleceń</Button>
                  <Button type="button" variant="secondary" onClick={() => setEditingWorker(worker)}>Edytuj</Button>
                  {worker.active ? (
                    <Button type="button" variant="danger" disabled={busy} onClick={() => deactivateWorker(worker)}>Dezaktywuj</Button>
                  ) : (
                    <Button type="button" variant="secondary" disabled={busy} onClick={() => activateWorker(worker)}>Aktywuj ponownie</Button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      </section>

      {workerLinks.length > 0 && (
        <details className="panel temporary-links-panel">
          <summary>Wykonawcy przypisani linkiem <span>{workerLinks.length}</span></summary>
          <div className="member-list worker-link-list worker-link-list--compact">
            {workerLinks.map((link) => (
              <article key={link.id}>
                <span>{link.label.slice(0, 2).toUpperCase()}</span>
                <div>
                  <strong>{link.label}</strong>
                  <small>{link.email || "Bez e-maila"} · {link.project_name || "Inwestycja"} · link-only</small>
                </div>
                <b>{link.revoked_at ? "Odwołany" : "Aktywny"}</b>
              </article>
            ))}
          </div>
        </details>
      )}

      {showAddContractor && (
        <Modal title="Dodaj wykonawcę" onClose={() => setShowAddContractor(false)}>
          <form className="form-stack" onSubmit={createWorker}>
            <p className="form-intro">
              E-mail jest opcjonalny. Jeśli go podasz, utworzymy zaproszenie do stałego konta wykonawcy.
            </p>
            <label>Typ<select name="profile_kind" defaultValue="craftsman"><option value="craftsman">Wykonawca</option><option value="crew">Firma / ekipa zewnętrzna</option></select></label>
            <label>Nazwa wykonawcy<input name="label" required placeholder="np. Firma remontowa albo hydraulik" autoFocus /></label>
            <div className="form-row">
              <label>E-mail opcjonalnie<input type="email" name="email" placeholder="Możesz zostawić puste" /></label>
              <label>Telefon opcjonalnie<input name="phone" /></label>
            </div>
            <label>Profesja / specjalizacja wykonawcy<textarea name="note" rows={2} placeholder="np. hydraulik, ogrodnik, glazurnik" /></label>
            <Button type="submit" busy={busy} icon="plus">Dodaj wykonawcę</Button>
          </form>
        </Modal>
      )}

      {previewWorker && (
        <Modal title={`Wykonawca: ${previewWorker.label}`} onClose={() => setPreviewWorker(null)} wide>
          <div className="worker-preview">
            <span className={`contractor-card__avatar ${previewWorker.profile_kind === "crew" ? "contractor-card__avatar--crew" : ""}`}>
              {previewWorker.label.slice(0, 2).toUpperCase()}
            </span>
            <div>
              <h3>{previewWorker.label}</h3>
              <p>{previewWorker.profile_kind === "crew" ? "Firma / ekipa zewnętrzna" : "Wykonawca"} · {previewWorker.active ? "Aktywny" : "Nieaktywny"}</p>
            </div>
            <div className="worker-preview__actions">
              <Button type="button" variant="secondary" onClick={() => { setEditingWorker(previewWorker); setPreviewWorker(null); }}>Edytuj</Button>
              {previewWorker.active ? (
                <Button type="button" variant="danger" disabled={busy} onClick={() => { const worker = previewWorker; setPreviewWorker(null); void deactivateWorker(worker); }}>Dezaktywuj</Button>
              ) : (
                <Button type="button" variant="secondary" disabled={busy} onClick={() => { const worker = previewWorker; setPreviewWorker(null); void activateWorker(worker); }}>Aktywuj</Button>
              )}
            </div>
            <dl>
              <div><dt>Profesja / specjalizacja</dt><dd>{previewWorker.note || (previewWorker.profile_kind === "crew" ? "Firma / ekipa zewnętrzna" : "Wykonawca")}</dd></div>
              <div><dt>E-mail</dt><dd>{previewWorker.email || "Nie podano"}</dd></div>
              <div><dt>Telefon</dt><dd>{previewWorker.phone || "Nie podano"}</dd></div>
              <div><dt>Konto</dt><dd>{previewWorker.account_type === "account" ? "konto po potwierdzeniu e-mail" : "link-only"}</dd></div>
            </dl>
            <div className="worker-preview__projects">
              <WorkerProjectSection title="Aktywne zlecenia" emptyText="Brak aktywnych zleceń" projects={[...previewActiveProjects, ...previewOtherProjects]} onOpen={openPreviewProject} />
              <WorkerProjectSection title="Historyczne zlecenia" emptyText="Brak zakończonych zleceń" projects={previewHistoryProjects} onOpen={openPreviewProject} />
              {previewAssignedOnly.length > 0 && (
                <section className="worker-project-section">
                  <header><h4>Zlecenia bez pełnych danych</h4><span>{previewAssignedOnly.length}</span></header>
                  <div className="worker-project-list">
                    {previewAssignedOnly.map((project) => (
                      <article className="worker-project-card worker-project-card--compact" key={project.id}>
                        <div>
                          <strong>{project.name}</strong>
                          <span className={`status status--${project.status}`}>{statusLabels[project.status] || project.status}</span>
                        </div>
                        <p>Brak szczegółów terminów i kwoty w obecnym payloadzie.</p>
                      </article>
                    ))}
                  </div>
                </section>
              )}
            </div>
          </div>
        </Modal>
      )}

      {editingWorker && (
        <Modal title="Edytuj wykonawcę" onClose={() => setEditingWorker(null)}>
          <form className="form-stack" onSubmit={saveWorker}>
            <label>Typ<select name="profile_kind" defaultValue={editingWorker.profile_kind}><option value="craftsman">Wykonawca</option><option value="crew">Firma / ekipa zewnętrzna</option></select></label>
            <label>Nazwa wykonawcy<input name="label" defaultValue={editingWorker.label} required autoFocus /></label>
            <label>E-mail opcjonalnie<input type="email" name="email" defaultValue={editingWorker.email} placeholder="Możesz zostawić puste" /></label>
            <label>Telefon<input name="phone" defaultValue={editingWorker.phone} /></label>
            <label>Profesja / specjalizacja wykonawcy<textarea name="note" rows={3} defaultValue={editingWorker.note} placeholder="np. hydraulik, ogrodnik, glazurnik" /></label>
            <Button type="submit" busy={busy}>Zapisz wykonawcę</Button>
          </form>
        </Modal>
      )}
    </>
  );
}

function TeamWorkerCard({
  worker,
  busy,
  onPreview,
  onEdit,
  onDeactivate,
  onActivate,
}: {
  worker: WorkerProfile;
  busy: boolean;
  onPreview: (worker: WorkerProfile) => void;
  onEdit: (worker: WorkerProfile) => void;
  onDeactivate: (worker: WorkerProfile) => void;
  onActivate: (worker: WorkerProfile) => void;
}) {
  const assignedCount = worker.assigned_projects.length;
  const note = worker.note || "Brak specjalizacji";
  return (
    <article className={`team-worker-card ${!worker.active ? "is-muted" : ""}`}>
      <div className={`team-worker-card__avatar ${worker.profile_kind === "crew" ? "team-worker-card__avatar--crew" : ""}`}>
        {worker.profile_kind === "crew" ? <Icon name="users" size={21} /> : worker.label.slice(0, 2).toUpperCase()}
      </div>
      <div className="team-worker-card__main">
        <div className="team-worker-card__top">
          <strong>{worker.label}</strong>
          <span>{workerKindLabel(worker)}</span>
        </div>
        <small>{note}</small>
        <small>{worker.email || "Bez e-maila"}{worker.phone ? ` · ${worker.phone}` : " · Telefon nie podano"}</small>
      </div>
      <div className="team-worker-card__status">
        <span className={`status ${worker.active ? "status--active" : "status--archived"}`}>{worker.active ? "Aktywny" : "Nieaktywny"}</span>
        <small>{workerAccountLabel(worker)}</small>
      </div>
      <div className="team-worker-card__assignments">
        <strong>{assignedCount}</strong>
        <small>{assignedCount === 1 ? "zlecenie" : "zleceń"}</small>
        {assignedCount === 0 && <span>Brak zleceń</span>}
      </div>
      <div className="team-worker-card__actions">
        <Button type="button" variant="secondary" onClick={() => onPreview(worker)}>Podgląd zleceń</Button>
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

function WorkerProjectSection({
  title,
  emptyText,
  projects,
  onOpen,
}: {
  title: string;
  emptyText: string;
  projects: Project[];
  onOpen: (project: Project) => void;
}) {
  return (
    <section className="worker-project-section">
      <header><h4>{title}</h4><span>{projects.length}</span></header>
      {projects.length === 0 ? (
        <p className="empty-note">{emptyText}</p>
      ) : (
        <div className="worker-project-list">
          {projects.map((project) => (
            <article className="worker-project-card" key={project.id}>
              <div className="worker-project-card__main">
                <strong>{project.name}</strong>
                <span className={`status status--${project.status}`}>{statusLabels[project.status] || project.status}</span>
              </div>
              <dl>
                <div><dt>Start</dt><dd>{formatContractDate(project.planned_start_date) || "Nie ustawiono"}</dd></div>
                <div><dt>Koniec</dt><dd>{formatContractDate(project.planned_end_date) || "Nie ustawiono"}</dd></div>
                <div><dt>Kwota</dt><dd>{contractAmountLabel(project) || "Nie podano"}</dd></div>
              </dl>
              <Button type="button" variant="secondary" onClick={() => onOpen(project)}>Otwórz zlecenie</Button>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

function CompanyTeamPanel({
  workspaceId,
  projects,
  onOpenProject,
  onChanged,
  notify,
}: {
  workspaceId: string;
  projects: Project[];
  onOpenProject: (project: Project) => void;
  onChanged: () => void;
  notify: (toast: Toast) => void;
}) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [invitationUrl, setInvitationUrl] = useState("");
  const [editingWorker, setEditingWorker] = useState<WorkerProfile | null>(null);
  const [previewWorker, setPreviewWorker] = useState<WorkerProfile | null>(null);
  const [showAddWorker, setShowAddWorker] = useState(false);
  const [teamSearch, setTeamSearch] = useState("");
  const [teamTypeFilter, setTeamTypeFilter] = useState<"all" | "crew" | "craftsman">("all");
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
      setShowAddWorker(false);
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
  const workerLinks = workspace.worker_links || [];
  const teamQuery = teamSearch.trim().toLowerCase();
  const filteredTeamWorkers = workers.filter((worker) => {
    const matchesQuery = `${worker.label} ${worker.email || ""} ${worker.phone || ""} ${worker.note || ""} ${worker.assigned_projects.map((project) => project.name).join(" ")}`
      .toLowerCase()
      .includes(teamQuery);
    const matchesType = teamTypeFilter === "all" || worker.profile_kind === teamTypeFilter;
    return matchesQuery && matchesType;
  });
  const allCrews = workers.filter((worker) => worker.profile_kind === "crew");
  const allCraftsmen = workers.filter((worker) => worker.profile_kind !== "crew");
  const activeWorkers = workers.filter((worker) => worker.active);
  const inactiveWorkers = workers.filter((worker) => !worker.active);
  const previewProjects = previewWorker
    ? projects.filter((project) => project.worker_profile_id === previewWorker.id || project.worker_profile?.id === previewWorker.id)
    : [];
  const previewActiveProjects = previewProjects.filter((project) => ["assigned", "in_progress"].includes(project.status));
  const previewHistoryProjects = previewProjects.filter((project) => project.status === "completed");
  const previewOtherProjects = previewProjects.filter((project) => !["assigned", "in_progress", "completed"].includes(project.status));
  const previewAssignedOnly = previewWorker
    ? previewWorker.assigned_projects.filter((assigned) => !previewProjects.some((project) => project.id === assigned.id))
    : [];

  function openPreviewProject(project: Project) {
    setPreviewWorker(null);
    onOpenProject(project);
  }

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
            <Button type="button" icon="plus" onClick={() => setShowAddWorker(true)}>Dodaj majstra / ekipę</Button>
          </div>
        </div>
        {invitationUrl && <div className="share-result share-result--compact"><input value={invitationUrl} readOnly /><Button variant="secondary" onClick={() => void copyToClipboard(invitationUrl)}>Kopiuj link</Button></div>}
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

      <section className="team-action-grid">
        <button type="button" className="team-action-card" onClick={() => setTeamTypeFilter("crew")}>
          <span><Icon name="users" size={34} /></span>
          <div>
            <h2>Zarządzaj ekipami</h2>
            <p>Twórz i zarządzaj ekipami majstrów. Przypisuj zlecenia i śledź ich realizację.</p>
          </div>
          <b>{allCrews.length}</b>
        </button>
        <button type="button" className="team-action-card team-action-card--blue" onClick={() => setTeamTypeFilter("craftsman")}>
          <span><Icon name="users" size={34} /></span>
          <div>
            <h2>Zarządzaj pojedynczymi majstrami</h2>
            <p>Dodawaj pojedynczych majstrów, przypisuj role i zlecenia.</p>
          </div>
          <b>{allCraftsmen.length}</b>
        </button>
      </section>

      <section className="panel directory-panel team-directory-panel">
        <div className="panel__header">
          <div>
            <h2>Lista majstrów i ekip</h2>
            <p>{activeWorkers.length} aktywnych, {inactiveWorkers.length} nieaktywnych.</p>
          </div>
          <Button type="button" icon="plus" onClick={() => setShowAddWorker(true)}>Dodaj majstra / ekipę</Button>
        </div>
        <div className="directory-toolbar">
          <input
            type="search"
            value={teamSearch}
            onChange={(event) => setTeamSearch(event.target.value)}
            placeholder="Szukaj majstra lub ekipy"
          />
          <div className="filter-pills" aria-label="Filtr typu wykonawcy">
            <button type="button" className={teamTypeFilter === "all" ? "is-active" : ""} onClick={() => setTeamTypeFilter("all")}>Wszyscy <span>{workers.length}</span></button>
            <button type="button" className={teamTypeFilter === "crew" ? "is-active" : ""} onClick={() => setTeamTypeFilter("crew")}>Ekipy <span>{allCrews.length}</span></button>
            <button type="button" className={teamTypeFilter === "craftsman" ? "is-active" : ""} onClick={() => setTeamTypeFilter("craftsman")}>Majstrowie <span>{allCraftsmen.length}</span></button>
          </div>
        </div>
        <div className="team-worker-table-head" aria-hidden="true">
          <span>Nazwa</span>
          <span>Status</span>
          <span>Przypisane zlecenia</span>
          <span>Akcje</span>
        </div>
        <div className="team-worker-list team-worker-list--table">
          {filteredTeamWorkers.length === 0 ? <p className="empty-note">Brak majstrów lub ekip dla wybranych filtrów.</p> : filteredTeamWorkers.map((worker) => (
            <TeamWorkerCard key={worker.id} worker={worker} busy={busy} onPreview={setPreviewWorker} onEdit={setEditingWorker} onDeactivate={deactivateWorker} onActivate={activateWorker} />
          ))}
        </div>
      </section>

      {workerLinks.length > 0 && (
        <details className="panel temporary-links-panel">
          <summary>Linki tymczasowe <span>{workerLinks.length}</span></summary>
          <div className="member-list worker-link-list worker-link-list--compact">
            {workerLinks.map((link) => (
              <article key={link.id}>
                <span>{link.label.slice(0, 2).toUpperCase()}</span>
                <div>
                  <strong>{link.label}</strong>
                  <small>{link.email || "Bez e-maila"} · {link.project_name || "Zlecenie"} · link-only</small>
                </div>
                <b>{link.revoked_at ? "Odwołany" : "Aktywny"}</b>
              </article>
            ))}
          </div>
        </details>
      )}

      {showAddWorker && (
        <Modal title="Dodaj majstra / ekipę" onClose={() => setShowAddWorker(false)}>
          <form className="form-stack" onSubmit={createWorker}>
            <p className="form-intro">
              Podanie e-maila oznacza zaproszenie do stałego konta po potwierdzeniu kodem.
              Bez e-maila dodasz wykonawcę do listy, a link tymczasowy wyślesz z poziomu zlecenia.
            </p>
            <label>Typ<select name="profile_kind" defaultValue="craftsman"><option value="craftsman">Majster</option><option value="crew">Ekipa</option></select></label>
            <label>Nazwa<input name="label" required placeholder="np. Mieciu hydraulik albo Ekipa Kowalskiego" autoFocus /></label>
            <div className="form-row">
              <label>E-mail opcjonalnie<input type="email" name="email" placeholder="Możesz zostawić puste" /></label>
              <label>Telefon opcjonalnie<input name="phone" /></label>
            </div>
            <label>Profesja / specjalizacja majstra lub ekipy<textarea name="note" rows={2} placeholder="np. hydraulik, ogrodnik, glazurnik" /></label>
            <Button type="submit" busy={busy} icon="plus">Dodaj majstra / ekipę</Button>
          </form>
        </Modal>
      )}

      {previewWorker && (
        <Modal title={`${workerKindLabel(previewWorker)}: ${previewWorker.label}`} onClose={() => setPreviewWorker(null)} wide>
          <div className="worker-preview">
            <span className={`team-worker-card__avatar ${previewWorker.profile_kind === "crew" ? "team-worker-card__avatar--crew" : ""}`}>
              {previewWorker.profile_kind === "crew" ? <Icon name="users" /> : previewWorker.label.slice(0, 2).toUpperCase()}
            </span>
            <div>
              <h3>{previewWorker.label}</h3>
              <p>{workerKindLabel(previewWorker)} · {previewWorker.active ? "Aktywny" : "Nieaktywny"}</p>
            </div>
            <div className="worker-preview__actions">
              <Button type="button" variant="secondary" onClick={() => { setEditingWorker(previewWorker); setPreviewWorker(null); }}>Edytuj</Button>
              {previewWorker.active ? (
                <Button type="button" variant="danger" disabled={busy} onClick={() => { const worker = previewWorker; setPreviewWorker(null); void deactivateWorker(worker); }}>Dezaktywuj</Button>
              ) : (
                <Button type="button" variant="secondary" disabled={busy} onClick={() => { const worker = previewWorker; setPreviewWorker(null); void activateWorker(worker); }}>Aktywuj</Button>
              )}
            </div>
            <dl>
              <div><dt>Profesja / specjalizacja</dt><dd>{previewWorker.note || "Brak specjalizacji"}</dd></div>
              <div><dt>E-mail</dt><dd>{previewWorker.email || "Nie podano"}</dd></div>
              <div><dt>Telefon</dt><dd>{previewWorker.phone || "Nie podano"}</dd></div>
              <div><dt>Konto</dt><dd>{workerAccountLabel(previewWorker)}</dd></div>
            </dl>
            <div className="worker-preview__projects">
              <WorkerProjectSection title="Aktywne zlecenia" emptyText="Brak aktywnych zleceń" projects={[...previewActiveProjects, ...previewOtherProjects]} onOpen={openPreviewProject} />
              <WorkerProjectSection title="Historia zleceń" emptyText="Brak zakończonych zleceń" projects={previewHistoryProjects} onOpen={openPreviewProject} />
              {previewAssignedOnly.length > 0 && (
                <section className="worker-project-section">
                  <header><h4>Zlecenia bez pełnych danych</h4><span>{previewAssignedOnly.length}</span></header>
                  <div className="worker-project-list">
                    {previewAssignedOnly.map((project) => (
                      <article className="worker-project-card worker-project-card--compact" key={project.id}>
                        <div>
                          <strong>{project.name}</strong>
                          <span className={`status status--${project.status}`}>{statusLabels[project.status] || project.status}</span>
                        </div>
                        <p>Brak szczegółów terminów i kwoty w obecnym payloadzie.</p>
                      </article>
                    ))}
                  </div>
                </section>
              )}
            </div>
          </div>
        </Modal>
      )}

      {editingWorker && (
        <Modal title="Edytuj majstra / ekipę" onClose={() => setEditingWorker(null)}>
          <form className="form-stack" onSubmit={saveWorker}>
            <label>Typ<select name="profile_kind" defaultValue={editingWorker.profile_kind}><option value="craftsman">Majster / pojedynczy wykonawca</option><option value="crew">Ekipa</option></select></label>
            <label>Nazwa<input name="label" defaultValue={editingWorker.label} required autoFocus /></label>
            <label>E-mail opcjonalnie<input type="email" name="email" defaultValue={editingWorker.email} placeholder="Możesz zostawić puste" /></label>
            <label>Telefon<input name="phone" defaultValue={editingWorker.phone} /></label>
            <label>Profesja / specjalizacja majstra lub ekipy<textarea name="note" rows={3} defaultValue={editingWorker.note} placeholder="np. hydraulik, ogrodnik, glazurnik" /></label>
            <Button type="submit" busy={busy}>Zapisz</Button>
          </form>
        </Modal>
      )}
    </>
  );
}

function TeamPage({
  user,
  projects,
  onProject,
  onUserUpdated,
  notify,
}: {
  user: User;
  projects: Project[];
  onProject: (project: Project) => void;
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
  const peopleLabels = peopleLabelsForUser(user);
  const primaryTeamAction = user.profile_type === "investor"
    ? peopleLabels.addAction
    : user.workspaces.length > 0
      ? peopleLabels.addAction
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
            <span className="eyebrow">Zespół firmy</span>
            <h1>{peopleLabels.section}</h1>
            <p>Zarządzaj ekipami i pojedynczymi majstrami bez wybierania wielu firm.</p>
          </div>
        </header>
        <CompanyTeamPanel workspaceId={user.workspaces[0].id} projects={projects} onOpenProject={onProject} onChanged={refreshUser} notify={notify} />
      </div>
    );
  }
  if (user.profile_type === "investor" && user.workspaces.length > 0) {
    return (
      <div className="page">
        <header className="page-header">
          <div>
            <span className="eyebrow">Współpraca</span>
            <h1>Wykonawcy</h1>
            <p>Dodawaj wykonawców, wyszukuj ich po nazwie i przypisuj do inwestycji.</p>
          </div>
        </header>
        <InvestorContractorsPanel workspaceId={user.workspaces[0].id} projects={projects} onOpenProject={onProject} onChanged={refreshUser} notify={notify} />
      </div>
    );
  }
  return (
    <div className="page">
      <header className="page-header"><div><span className="eyebrow">{peopleLabels.section}</span><h1>{peopleLabels.section}</h1><p>{user.profile_type === "investor" ? "Dodawaj wykonawców, wybieraj ich przy zleceniu i wysyłaj im link do postępu." : "Edytuj dane firmy, dodawaj majstrów i wysyłaj im link do logowania."}</p></div><Button icon="plus" onClick={openTeamAction}>{primaryTeamAction}</Button></header>
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
  uiMode,
  onUiModeChange,
}: {
  user: User;
  onUpdated: (user: User) => void;
  onLogout: () => void;
  notify: (toast: Toast) => void;
  uiMode: UiMode;
  onUiModeChange: (mode: UiMode) => void;
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
  if (isCompanyWorker(user)) {
    const assignedWorkspace = user.workspaces[0];
    return (
      <div className="page worker-settings-page">
        <WorkerMobileHeader />
        <header className="worker-page-header">
          <div>
            <span className="eyebrow">Konto pracownika</span>
            <h1>Ustawienia</h1>
            <p>Proste dane konta majstra firmy. Bez ustawień firmy i bez zarządzania ekipami.</p>
          </div>
        </header>
        <section className="worker-settings-stack">
          <article className="worker-settings-card">
            <h2>Moje konto</h2>
            <form className="worker-settings-form" onSubmit={submit}>
              <label><span><Icon name="users" /></span><div><strong>Imię i nazwisko</strong><input name="name" defaultValue={user.name} placeholder="Jan Kowalski" /></div></label>
              <label><span><Icon name="send" /></span><div><strong>E-mail / login</strong><input value={user.email || "Konto dostępowe bez e-maila"} disabled /></div></label>
              <label><span><Icon name="settings" /></span><div><strong>Telefon</strong><input name="phone" defaultValue={user.phone} placeholder="+48 600 000 000" /></div></label>
              <input type="hidden" name="preferred_mode" value={user.preferred_mode || "field"} />
              <Button type="submit">Zapisz profil</Button>
            </form>
          </article>
          <article className="worker-settings-card">
            <h2>Przypisana firma</h2>
            <div className="worker-settings-row">
              <span><Icon name="users" /></span>
              <div>
                <strong>{assignedWorkspace?.name || "Brak przypisanej firmy"}</strong>
                <small>{assignedWorkspace?.role ? `Twoja rola: ${assignedWorkspace.role}` : "Firma pojawi się tutaj po przypisaniu przez szefa."}</small>
              </div>
            </div>
          </article>
          <article className="worker-settings-card">
            <h2>Preferencje</h2>
            <div className="worker-settings-note worker-settings-mode">
              <div>
                <strong>Tryb domyślny</strong>
                <p>Tryb uruchamiany po zalogowaniu.</p>
              </div>
              <WorkerModeSwitch uiMode={uiMode} onUiModeChange={onUiModeChange} />
            </div>
          </article>
          <article className="worker-settings-card">
            <h2>Bezpieczeństwo</h2>
            <div className="worker-settings-note">
              <strong>Hasło, kody i aktywacja linkiem będą osobnym krokiem.</strong>
              <p>Ten redesign nie zmienia logowania. Flow dla majstrów bez e-maila zostaje na KROK 10B/10C.</p>
            </div>
          </article>
          <Button type="button" variant="danger" onClick={onLogout}>Wyloguj się</Button>
        </section>
      </div>
    );
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
  const { completedCount, progress } = projectStageProgress(project);
  const stagesCount = project.stages?.length || 0;
  const formatter = new Intl.DateTimeFormat("pl", { day: "2-digit", month: "2-digit", year: "numeric" });
  const timeFormatter = new Intl.DateTimeFormat("pl", { dateStyle: "medium", timeStyle: "short" });
  const latestDate = entries[0]?.occurred_at || project.updated_at || project.created_at;
  const latestImages = entries
    .flatMap((entry) => entry.media.filter((asset) => asset.kind === "image").map((asset) => ({ ...asset, entry })))
    .slice(0, 6);
  const heroImage = latestImages[0];
  const completedItems = entries
    .filter((entry) => entry.kind !== "problem" && (entry.body || entry.transcript))
    .slice(0, 4);
  const contractorName = project.worker_profile?.label || "Nie podano";
  const safeStatusLabel = statusLabels[project.status] || project.status;
  const formatDate = (value?: string | null) => value ? formatter.format(new Date(value)) : "Nie ustawiono";
  return (
    <div className="client-project">
      <header className="client-hero">
        <div className="client-hero__brand">
          <Logo />
          <div>
            <h1>Pan Majster</h1>
            <p>Podsumowanie dla klienta</p>
          </div>
        </div>
        <Button variant="secondary" icon="sync" onClick={() => void load()}>Odśwież</Button>
      </header>
      <main>
        <section className="client-summary-card">
          <div className="client-summary-card__image">
            {heroImage ? (
              <button type="button" onClick={() => setLightbox({ src: withPin(heroImage.url), alt: heroImage.original_name })}>
                <img src={withPin(heroImage.url)} alt={heroImage.original_name} loading="lazy" />
              </button>
            ) : (
              <div className="client-image-placeholder"><Icon name="clipboard" /><span>Podgląd zlecenia</span></div>
            )}
          </div>
          <div className="client-summary-card__content">
            <span className={`status status--${project.status}`}>{safeStatusLabel}</span>
            <h2>{project.name}</h2>
            <p>{project.description || project.address || project.client_name || "Aktualny podgląd postępu prac."}</p>
            <div className="client-progress">
              <div><span style={{ width: `${progress}%` }} /></div>
              <strong>{progress}%</strong>
            </div>
            {stagesCount > 0 && <small>{completedCount} z {stagesCount} etapów ukończonych</small>}
          </div>
        </section>

        <section className="client-info-card">
          <div><Icon name="clipboard" /><span>Planowany start</span><strong>{formatDate(project.planned_start_date)}</strong></div>
          <div><Icon name="clipboard" /><span>Planowany koniec</span><strong>{formatDate(project.planned_end_date)}</strong></div>
          <div><Icon name="sync" /><span>Ostatnia aktualizacja</span><strong>{timeFormatter.format(new Date(latestDate))}</strong></div>
          <div><Icon name="users" /><span>Wykonawca</span><strong>{contractorName}</strong></div>
        </section>

        {project.contract_amount && <ContractTermsPanel project={project} />}

        <section className="client-section-card client-done-card">
          <span className="client-section-icon client-section-icon--green"><Icon name="check" /></span>
          <div>
            <h2>Co zostało wykonane do tej pory</h2>
            {completedItems.length === 0 ? (
              <p className="client-muted">Pierwsze opisy postępu pojawią się tutaj po dodaniu aktualizacji.</p>
            ) : (
              <ul>
                {completedItems.map((entry) => (
                  <li key={entry.id}><Icon name="check" /><span>{entry.body || entry.transcript}</span></li>
                ))}
              </ul>
            )}
          </div>
        </section>

        <section className="client-section-card client-photos-card">
          <span className="client-section-icon client-section-icon--green"><Icon name="camera" /></span>
          <div>
            <h2>Zdjęcia z prac</h2>
            {latestImages.length === 0 ? (
              <p className="client-muted">Zdjęcia pojawią się tutaj po pierwszej aktualizacji z terenu.</p>
            ) : (
              <div className="client-photo-grid">
                {latestImages.map((asset) => {
                  const src = withPin(asset.url);
                  return (
                    <button type="button" className="media-button" onClick={() => setLightbox({ src, alt: asset.original_name })} key={asset.id}>
                      <img src={src} alt={asset.original_name} loading="lazy" />
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </section>

        <section className="client-section-card client-reports-card">
          <span className="client-section-icon client-section-icon--purple"><Icon name="report" /></span>
          <div>
            <h2>Raporty PDF</h2>
            {reports.length === 0 ? (
              <p className="client-muted">Gotowe raporty PDF pojawią się tutaj po publikacji.</p>
            ) : (
              <div className="client-report-list">
                {reports.map((report) => (
                  <a className="client-report-link" href={withPin(`/api/public/projects/${token}/reports/${report.id}/pdf`)} target="_blank" rel="noreferrer" key={report.id}>
                    <Icon name="report" />
                    <div>
                      <strong>{report.title}</strong>
                      <small>{report.published_at ? formatter.format(new Date(report.published_at)) : "Opublikowany raport"}</small>
                    </div>
                    <span>Otwórz</span>
                  </a>
                ))}
              </div>
            )}
          </div>
        </section>
      </main>
      <footer className="client-security-footer">
        <Icon name="settings" />
        <span>To bezpieczny podgląd postępu prac - bez logowania i bez dostępu do panelu wykonawcy.</span>
      </footer>
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
  const [uiMode, setUiMode] = useUiMode();
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

  const resetSessionView = useCallback(() => {
    setSelectedProject(null);
    setCreateOpen(false);
    setProjects([]);
    setSection("home");
  }, []);

  const enterAuthenticatedApp = useCallback((next: User) => {
    resetSessionView();
    setUser(next);
    setAuthOpen(false);
    navigate("/app");
  }, [resetSessionView]);

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
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      resetSessionView();
      setUser(null);
      setAuthOpen(false);
    }
    navigate("/");
  }

  if (currentRoute.kind === "client") return <PublicProject token={currentRoute.token} />;
  if (currentRoute.kind === "report") return <PublicReport token={currentRoute.token} />;
  if (currentRoute.kind === "portfolio") return <PublicPortfolio slug={currentRoute.slug} />;
  if (currentRoute.kind === "guest") {
    return <GuestEntry token={currentRoute.token} notify={notify} onQueue={refreshQueue} />;
  }
  if (currentRoute.kind === "invite" && !user) {
    return <InvitePage token={currentRoute.token} onSuccess={enterAuthenticatedApp} />;
  }

  const marketing = currentRoute.kind === "marketing" && !user;
  if (marketing) {
    return <><Marketing onLogin={() => setAuthOpen(true)} />{authOpen && <AuthModal onClose={() => setAuthOpen(false)} onSuccess={enterAuthenticatedApp} />}{toast && <ToastView toast={toast} />}</>;
  }
  if (loading || !user) {
    return <><div className="splash"><img src="/brand/app-icon.png" alt="Pan Majster" /><span className="spinner" /></div>{authOpen && <AuthModal onClose={() => { setAuthOpen(false); navigate("/"); }} onSuccess={enterAuthenticatedApp} />}</>;
  }
  if (!user.profile_type) {
    return <Onboarding onComplete={enterAuthenticatedApp} onBack={logout} />;
  }

  const visibleSection = visibleSectionForUser(user, section);
  const activeSection = selectedProject ? "projects" : visibleSection;
  const body = selectedProject ? (
    <ProjectView
      projectId={selectedProject.id}
      user={user}
      uiMode={uiMode}
      onUiModeChange={setUiMode}
      onBack={() => setSelectedProject(null)}
      onUnavailable={() => { setSelectedProject(null); setSection("projects"); }}
      notify={notify}
      onQueue={refreshQueue}
    />
  ) : visibleSection === "projects" ? (
    <ProjectsPage
      user={user}
      projects={projects}
      onProject={setSelectedProject}
      onCreate={() => setCreateOpen(true)}
      uiMode={uiMode}
      onUiModeChange={setUiMode}
      notify={notify}
      onQueue={refreshQueue}
      onChanged={loadProjects}
    />
  ) : visibleSection === "reports" ? (
    <ReportsPage user={user} projects={projects} onOpen={setSelectedProject} />
  ) : visibleSection === "team" ? (
    <TeamPage user={user} projects={projects} onProject={setSelectedProject} onUserUpdated={setUser} notify={notify} />
  ) : visibleSection === "settings" ? (
    <SettingsPage user={user} onUpdated={setUser} onLogout={logout} notify={notify} uiMode={uiMode} onUiModeChange={setUiMode} />
  ) : (
    <Dashboard user={user} projects={projects} onProject={setSelectedProject} onCreate={() => setCreateOpen(true)} uiMode={uiMode} />
  );

  return (
    <>
      <AppShell
        user={user}
        active={activeSection}
        onNavigate={(next) => { setSelectedProject(null); setSection(next); }}
        onLogout={logout}
        queueCount={queueCount}
        uiMode={uiMode}
        onUiModeChange={setUiMode}
        logo={<Logo />}
        compactLogo={<Logo compact />}
      >
        {body}
      </AppShell>
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

