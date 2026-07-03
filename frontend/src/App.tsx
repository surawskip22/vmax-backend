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
import { IndependentPortfolioPage } from "./IndependentPortfolioPage";
import {
  deleteQueuedEntry,
  queuedEntryCount,
  queueEntry,
  queuedEntries,
  type OfflineScopeKey,
  type QueuedEntry,
} from "./offline";
import {
  peopleLabelsForUser,
  profileLabels,
  workerKindLabel,
  workerKindLabelForUser,
} from "./roleLabels";
import { visibleSectionForUser } from "./RoleAwareSidebar";
import { filterServiceTags, serviceTags, tagBySlug } from "./serviceTaxonomy";
import type { ClientLink, Comment, Entry, MediaAsset, Project, Report, Stage, User, WorkerProfile, Workspace } from "./types";
import { useUiMode, type UiMode } from "./useUiMode";

type Toast = { kind: "success" | "error" | "info"; message: string };
type EntryTextTarget = "description" | "note";
type EntryModalState = { kind: "update" | "problem"; mode: "photo" | "audio" | "text" };
type CommentIntent = NonNullable<Comment["intent"]>;
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
type DemoAdminAccount = {
  email: string;
  password: string;
  label: string;
};
type DemoAdminDiagnostics = {
  database_fingerprint: string;
  app_env: string;
  reset_backend_marker: string;
  demo_users_found: number;
  demo_users_created?: number;
  projects_after_reset_by_owner: Record<string, number>;
  projects_visible_by_user: Record<string, number>;
  entries_visible_by_user: Record<string, number>;
  workspace_count: number;
  client_links: number;
  guest_links: number;
};
type DemoAdminResetResult = {
  status: string;
  counts: Record<string, number>;
  company_statuses: Record<string, number>;
  independent_statuses: Record<string, number>;
  investor_statuses: Record<string, number>;
  guest_links: number;
  client_links: number;
  demo_accounts: DemoAdminAccount[];
  diagnostics?: DemoAdminDiagnostics;
  note?: string;
};
type DemoAdminStatusResult = {
  status: string;
  enabled: boolean;
  reset_enabled: boolean;
  diagnostics: DemoAdminDiagnostics;
  demo_accounts: DemoAdminAccount[];
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
const LIVE_TRANSCRIPTION_FALLBACK_MESSAGE =
  "Transkrypcja na żywo jest niedostępna na tym urządzeniu. Nagranie audio zostanie zapisane.";

function hashOfflineToken(value: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

function userOfflineScope(user: User | null | undefined): OfflineScopeKey | null {
  if (!user) return null;
  if (user.id) return `user:${user.id}`;
  const email = user.email?.trim().toLowerCase();
  return email ? `user:${user.profile_type || "user"}:${email}` : null;
}

function guestOfflineScope(token: string | null | undefined): OfflineScopeKey | null {
  const cleanToken = token?.trim();
  return cleanToken ? `guest:${hashOfflineToken(cleanToken)}` : null;
}

async function syncQueuedEntriesForScope(scopeKey: OfflineScopeKey): Promise<boolean> {
  const queue = await queuedEntries(scopeKey);
  let syncedAny = false;
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
      syncedAny = true;
    } catch {
      break;
    }
  }
  return syncedAny;
}

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

function investorContractorTypeLabel(worker: WorkerProfile): string {
  return worker.account_type === "account" ? "Konto Pan Majster" : "Przez link";
}

function investorContractorActivityLabel(worker: WorkerProfile): string {
  const value = worker.updated_at || worker.created_at;
  if (!value) return "Brak aktywności";
  return new Intl.DateTimeFormat("pl").format(new Date(value));
}

function investorContractorCount(user: User, projects: Project[]): number {
  const ids = new Set<string>();
  user.workspaces.forEach((workspace) => {
    workspace.worker_profiles?.forEach((worker) => ids.add(`profile:${worker.id}`));
    workspace.worker_links?.forEach((link) => ids.add(`link:${link.id}`));
  });
  projects.forEach((project) => {
    if (project.worker_profile?.id) ids.add(`profile:${project.worker_profile.id}`);
    project.worker_links?.forEach((link) => ids.add(`link:${link.id}`));
  });
  return ids.size;
}

function projectHasLinkOnlyWorker(project: Project): boolean {
  return Boolean(
    project.worker_profile?.account_type === "link_only" ||
    project.worker_links?.some((link) => !link.revoked_at),
  );
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
      eyebrow: "Moje inwestycje",
      title: "Inwestycje / Zlecenia",
      description: "Zarządzaj inwestycjami i pracami, które zlecasz wykonawcom.",
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
  if (user.profile_type === "company_owner") {
    return {
      eyebrow: "Firma",
      title: "Zlecenia",
      description: "Zarządzaj zleceniami firmy, ekipami i postępem prac.",
      createLabel: "Dodaj zlecenie",
      searchPlaceholder: "Szukaj zlecenia, klienta, majstra, ekipy lub adresu...",
      emptyTitle: "Dodaj pierwsze zlecenie",
      emptyText: "Zlecenie połączy klienta, ekipę, terminy, kwotę i historię postępu.",
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
const commentIntentLabels: Record<CommentIntent, string> = {
  comment: "Komentarz",
  confirm_resolved: "Klient potwierdził rozwiązanie",
  still_open: "Klient zgłosił dalszy problem",
  suggest_solution: "Sugestia klienta",
};
const contractTermsDisclaimer = "To informacja umowna. To nie jest faktura, platnosc ani wezwanie do zaplaty.";

function commentAuthorLabel(comment: Comment): string {
  return comment.author_label || comment.author?.name || comment.author?.email || comment.guest_label || "Komentarz";
}

function commentIntentLabel(comment: Comment): string {
  return commentIntentLabels[comment.intent || "comment"];
}

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
  const [step, setStep] = useState<"email" | "code" | "password" | "demoAdmin">("email");
  const [email, setEmail] = useState(initialEmail);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [devCode, setDevCode] = useState("");
  const [demoAdminUser, setDemoAdminUser] = useState("");
  const [demoAdminPassword, setDemoAdminPassword] = useState("");
  const [demoAdminToken, setDemoAdminToken] = useState("");
  const [demoAdminAccounts, setDemoAdminAccounts] = useState<DemoAdminAccount[]>([]);
  const [demoAdminConfirmation, setDemoAdminConfirmation] = useState("");
  const [demoAdminResult, setDemoAdminResult] = useState<DemoAdminResetResult | null>(null);
  const [demoAdminStatus, setDemoAdminStatus] = useState<DemoAdminStatusResult | null>(null);
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

  async function loginDemoAdmin(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setDemoAdminResult(null);
    try {
      const result = await api<{
        token: string;
        demo_accounts: DemoAdminAccount[];
        reset_enabled: boolean;
      }>("/demo-admin/login", {
        method: "POST",
        body: JSON.stringify({
          username: demoAdminUser,
          password: demoAdminPassword,
        }),
      });
      setDemoAdminToken(result.token);
      setDemoAdminAccounts(result.demo_accounts || []);
      setDemoAdminPassword("");
      await loadDemoAdminStatus(result.token);
      if (!result.reset_enabled) {
        setError("Panel demo działa, ale reset wymaga ALLOW_DEMO_RESET=1 po stronie backendu.");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Nie udało się zalogować do panelu demo");
    } finally {
      setBusy(false);
    }
  }

  async function resetDemoData(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setDemoAdminResult(null);
    try {
      const result = await api<DemoAdminResetResult>("/demo-admin/reset", {
        method: "POST",
        headers: { Authorization: `Bearer ${demoAdminToken}` },
        body: JSON.stringify({ confirmation: demoAdminConfirmation }),
      });
      setDemoAdminResult(result);
      if (result.diagnostics) {
        setDemoAdminStatus({
          status: "ok",
          enabled: true,
          reset_enabled: true,
          diagnostics: result.diagnostics,
          demo_accounts: result.demo_accounts || demoAdminAccounts,
        });
      }
      setDemoAdminAccounts(result.demo_accounts || demoAdminAccounts);
      setDemoAdminConfirmation("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Nie udało się zresetować demo");
    } finally {
      setBusy(false);
    }
  }

  async function loadDemoAdminStatus(token = demoAdminToken) {
    if (!token) return;
    const result = await api<DemoAdminStatusResult>("/demo-admin/status", {
      headers: { Authorization: `Bearer ${token}` },
    });
    setDemoAdminStatus(result);
    setDemoAdminAccounts(result.demo_accounts || []);
  }

  async function refreshDemoAdminStatus() {
    setBusy(true);
    setError("");
    try {
      await loadDemoAdminStatus();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Nie udało się pobrać statusu demo");
    } finally {
      setBusy(false);
    }
  }

  function switchStep(nextStep: "email" | "code" | "password" | "demoAdmin") {
    setError("");
    setStep(nextStep);
  }

  const demoDiagnostics = demoAdminStatus?.diagnostics || demoAdminResult?.diagnostics;

  return (
    <Modal
      title={
        step === "email"
          ? "Wejdź do Pan Majster"
          : step === "password"
            ? "Logowanie testowe"
            : step === "demoAdmin"
              ? "Panel demo"
              : "Sprawdź pocztę"
      }
      onClose={onClose}
    >
      {step === "email" ? (
        <form className="form-stack" onSubmit={requestCode}>
          <p className="form-intro">Bez hasła. Wyślemy Ci jednorazowy kod logowania.</p>
          <label>
            Adres e-mail
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
          </label>
          {error && <p className="form-error">{error}</p>}
          <Button type="submit" busy={busy}>Wyślij kod</Button>
          <Button type="button" variant="ghost" onClick={() => switchStep("password")}>Mam hasło testowe</Button>
          <Button type="button" variant="ghost" onClick={() => switchStep("demoAdmin")}>Panel demo</Button>
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
          <Button type="button" variant="ghost" onClick={() => switchStep("email")}>Wróć do kodu e-mail</Button>
        </form>
      ) : step === "demoAdmin" ? (
        !demoAdminToken ? (
          <form className="form-stack demo-admin-panel" onSubmit={loginDemoAdmin}>
            <p className="form-intro">
              Panel służy wyłącznie do odtworzenia danych demo na środowisku testowym.
            </p>
            <label>
              Login panelu demo
              <input value={demoAdminUser} onChange={(event) => setDemoAdminUser(event.target.value)} required autoFocus />
            </label>
            <label>
              Hasło panelu demo
              <input
                type="password"
                value={demoAdminPassword}
                onChange={(event) => setDemoAdminPassword(event.target.value)}
                required
              />
            </label>
            {error && <p className="form-error">{error}</p>}
            <Button type="submit" busy={busy}>Wejdź do panelu demo</Button>
            <Button type="button" variant="ghost" onClick={() => switchStep("email")}>Wróć do logowania</Button>
          </form>
        ) : (
          <form className="form-stack demo-admin-panel" onSubmit={resetDemoData}>
            <p className="form-intro">
              Reset usuwa tylko dane demo i odtwarza realistyczny zestaw startowy. Konta demo zostają aktywne.
            </p>
            <div className="demo-admin-accounts">
              <strong>Konta demo po resecie</strong>
              {(demoAdminResult?.demo_accounts || demoAdminAccounts).map((account) => (
                <span key={account.email}>
                  <b>{account.label}</b>
                  <small>{account.email}</small>
                </span>
              ))}
              <small>Hasło kont demo: test1234</small>
            </div>
            {demoDiagnostics && (
              <div className="demo-admin-result">
                <strong>Status aktywnej bazy demo</strong>
                <span>Baza: {demoDiagnostics.database_fingerprint}</span>
                <span>Środowisko: {demoDiagnostics.app_env}</span>
                <span>Samodzielny: {demoDiagnostics.projects_visible_by_user["samodzielny@majster.pl"] || 0} zlecenia</span>
                <span>Szef firmy: {demoDiagnostics.projects_visible_by_user["szef@majster.pl"] || 0} zlecenia</span>
                <span>Inwestor: {demoDiagnostics.projects_visible_by_user["inwestor@majster.pl"] || 0} inwestycje</span>
                <span>Pracownik 1: {demoDiagnostics.projects_visible_by_user["pracownik@majster.pl"] || 0} zlecenia</span>
                <span>Pracownik 2: {demoDiagnostics.projects_visible_by_user["pracownik2@majster.pl"] || 0} zlecenia</span>
                <span>Linki klienta: {demoDiagnostics.client_links}</span>
                <span>Linki majstra: {demoDiagnostics.guest_links}</span>
              </div>
            )}
            <label>
              Potwierdzenie resetu
              <input
                value={demoAdminConfirmation}
                onChange={(event) => setDemoAdminConfirmation(event.target.value)}
                placeholder="RESET DEMO"
                required
              />
            </label>
            {demoAdminResult && (
              <div className="demo-admin-result">
                <strong>Dane demo odtworzone</strong>
                <span>Projekty: {demoAdminResult.counts.projects}</span>
                <span>Wpisy: {demoAdminResult.counts.entries}</span>
                <span>Media: {demoAdminResult.counts.media_assets || 0}</span>
                <span>Linki klienta: {demoAdminResult.client_links}</span>
                <span>Linki majstra: {demoAdminResult.guest_links}</span>
              </div>
            )}
            {error && <p className="form-error">{error}</p>}
            <Button type="submit" busy={busy} disabled={demoAdminConfirmation !== "RESET DEMO"}>
              Resetuj dane demo
            </Button>
            <Button type="button" variant="ghost" busy={busy} onClick={refreshDemoAdminStatus}>
              Odśwież status demo
            </Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setDemoAdminToken("");
                setDemoAdminResult(null);
                setDemoAdminConfirmation("");
              }}
            >
              Wyloguj z panelu demo
            </Button>
          </form>
        )
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
          <Button type="button" variant="ghost" onClick={() => switchStep("email")}>Zmień adres</Button>
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
    <Modal title={user.profile_type === "investor" ? "Nowa inwestycja" : "Nowe zlecenie"} onClose={onClose} wide>
      <form className="form-stack job-form" onSubmit={submit}>
        <p className="job-form-intro">
          Uzupełnij najważniejsze dane. Zapis nie zmienia uprawnień, linków ani raportów.
        </p>
        <section className="job-form-section">
          <header className="job-form-section__header">
            <span><Icon name="clipboard" /></span>
            <div>
              <h3>Dane</h3>
              <p>Nazwa, klient i lokalizacja zlecenia.</p>
            </div>
          </header>
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
        <p className="job-form-note">Adres pomaga zorganizować zlecenie. Nie jest widoczny publicznie.</p>
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
        </section>
        <section className="job-form-section">
          <header className="job-form-section__header">
            <span><Icon name="report" /></span>
            <div>
              <h3>Terminy i budżet</h3>
              <p>Planowane daty, tolerancja terminu i kwota umowna.</p>
            </div>
          </header>
          <div className="form-row">
            <label>Planowany start<input type="date" name="planned_start_date" /></label>
            <label>Planowany koniec<input type="date" name="planned_end_date" /></label>
          </div>
          <div className="form-row">
            <label>Niepewnosc terminu (+/- dni)<input type="number" name="schedule_uncertainty_days" min="0" step="1" placeholder="np. 3" /></label>
            <label>Kwota umowna (PLN)<input type="text" name="contract_amount" inputMode="decimal" placeholder="np. 12000" /></label>
          </div>
          <p className="job-form-note">{contractTermsDisclaimer}</p>
        </section>
        {!isInvestor(user) && user.workspaces.length > 0 && (
          <section className="job-form-section">
            <header className="job-form-section__header">
              <span><Icon name="users" /></span>
              <div>
                <h3>Dodatkowe opcje</h3>
                <p>Istniejące ustawienia przypisania zlecenia.</p>
              </div>
            </header>
            <label>
              Firma
              <select name="workspace_id" defaultValue="">
                <option value="">Projekt prywatny</option>
                {user.workspaces.map((workspace) => (
                  <option value={workspace.id} key={workspace.id}>{workspace.name}</option>
                ))}
              </select>
            </label>
          </section>
        )}
        <section className="job-form-section">
          <header className="job-form-section__header">
            <span><Icon name="send" /></span>
            <div>
              <h3>Opis</h3>
              <p>Krótki zakres prac widoczny w szczegółach zlecenia.</p>
            </div>
          </header>
          <label>Opis zlecenia<textarea name="description" rows={4} placeholder="Krótki zakres prac..." /></label>
        </section>
        {isIndependentContractor(user) && (
        <section className="job-form-section job-form-section--disabled job-form-portfolio-info">
          <header className="job-form-section__header">
            <span><Icon name="image" /></span>
            <div>
              <h3>Portfolio</h3>
              <p>Dodaj później do Mojej wizytówki</p>
            </div>
            <em>Dostępne po zakończeniu</em>
          </header>
          <p className="job-form-note">
            Po zakończeniu zlecenia możesz zrobić z niego publiczną realizację:
            wybrać zdjęcie główne, galerię i opis. To zlecenie nie zostanie
            opublikowane automatycznie.
          </p>
        </section>
        )}
        {error && <p className="form-error">{error}</p>}
        <div className="modal-actions job-form-actions">
          <Button type="button" variant="secondary" onClick={onClose}>Anuluj</Button>
          <Button type="submit" busy={busy} icon="plus">Utwórz {projectLabel}</Button>
        </div>
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
  const investorMode = isInvestor(user);
  const active = projects.filter((project) => ["assigned", "in_progress"].includes(project.status));
  const problems = projects.reduce((sum, item) => sum + (item.open_problem_count || 0), 0);
  const canCreate = canCreateProject(user);
  const intro =
    investorMode
      ? "Tu widzisz postęp swoich inwestycji, otwarte sprawy i ostatnie raporty."
      : isCompanyOwner(user)
        ? "Tu kontrolujesz zlecenia firmy, majstrów i zgłoszone problemy."
        : "Tu masz szybki podgląd swoich zleceń i raportów.";
  const createLabel = user.profile_type === "investor" ? "Dodaj inwestycję" : "Dodaj zlecenie";
  const dashboardTitle = investorMode ? "Dzień dobry, Inwestorze!" : `Dzień dobry${user.name ? `, ${user.name.split(" ")[0]}` : ""}!`;
  const recentTitle = investorMode ? "Ostatnie inwestycje / zlecenia" : "Ostatnie zlecenia";
  const recentText = investorMode
    ? "Prywatny podgląd inwestycji, wykonawców i ostatniej aktywności."
    : "Wybierz projekt, aby dodać zdjęcia lub raport.";
  const recentCreateLabel = investorMode ? "+ Dodaj inwestycję" : "+ Nowe zlecenie";
  const reportCount = projects.reduce((sum, project) => sum + (project.entry_count || 0), 0);
  const contractorCount = investorMode ? investorContractorCount(user, projects) : projects.length;
  return (
    <div className={`page dashboard ${investorMode ? "dashboard--investor" : ""}`}>
      <header className="page-header">
        <div>
          <span className="eyebrow">Środa, {new Intl.DateTimeFormat("pl", { day: "numeric", month: "long" }).format(new Date())}</span>
          <div className="role-inline">Typ konta: <strong>{user.profile_type ? profileLabels[user.profile_type] : "Nie wybrano"}</strong></div>
          <h1>{dashboardTitle}</h1>
          <p>{intro}</p>
        </div>
        {canCreate && <Button icon="plus" onClick={onCreate}>{createLabel}</Button>}
      </header>
      <div className={`stat-grid ${simpleMode ? "stat-grid--simple" : ""}`}>
        <article><span className="stat-icon stat-icon--blue"><Icon name="clipboard" /></span><div><small>{investorMode ? "Aktywne inwestycje" : "Aktywne zlecenia"}</small><strong>{active.length}</strong></div></article>
        <article><span className="stat-icon stat-icon--red"><Icon name="alert" /></span><div><small>Otwarte problemy</small><strong>{problems}</strong></div></article>
        {!simpleMode && <article><span className="stat-icon stat-icon--green"><Icon name="check" /></span><div><small>{investorMode ? "Ostatnie raporty" : "Zakończone"}</small><strong>{investorMode ? reportCount : projects.filter((p) => p.status === "completed").length}</strong></div></article>}
        {!simpleMode && <article><span className="stat-icon stat-icon--orange"><Icon name="users" /></span><div><small>{investorMode ? "Wykonawcy" : "Wszystkie projekty"}</small><strong>{contractorCount}</strong></div></article>}
      </div>
      <section className="panel">
        <div className="panel__header">
          <div><h2>{recentTitle}</h2><p>{recentText}</p></div>
          {canCreate && <button className="text-button" onClick={onCreate}>{recentCreateLabel}</button>}
        </div>
        {projects.length === 0 ? (
          <EmptyState icon="clipboard" title={investorMode ? "Dodaj pierwszą inwestycję" : "Dodaj pierwsze zlecenie"} text={investorMode ? "Inwestycja połączy wykonawcę, terminy, wpisy i raporty w jednym prywatnym panelu." : "Projekt połączy zdjęcia, opisy, problemy i raporty w jedną historię."}>
            {canCreate && <Button onClick={onCreate} icon="plus">{investorMode ? "Dodaj inwestycję" : "Utwórz zlecenie"}</Button>}
          </EmptyState>
        ) : (
          <div className="project-list">
            {projects.slice(0, simpleMode ? 5 : 8).map((project) => (
              <button key={project.id} className={`project-row ${simpleMode ? "project-row--simple" : ""}`} onClick={() => onProject(project)}>
                <span className="project-row__icon"><Icon name="clipboard" /></span>
                <div className="project-row__main">
                  <strong>{project.name}</strong>
                  <span>{investorMode ? (project.address || "Lokalizacja nieuzupełniona") : `${project.client_name || "Bez klienta"} · ${project.address || "Bez adresu"}`}</span>
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
  offlineScopeKey,
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
  offlineScopeKey?: OfflineScopeKey | null;
}) {
  const [filter, setFilter] = useState("");
  const [viewFilter, setViewFilter] = useState<"all" | "open" | "problems" | "history">("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [workerFilter, setWorkerFilter] = useState("all");
  const [sortBy, setSortBy] = useState<"newest" | "oldest" | "start" | "end" | "status">("newest");
  const simpleMode = uiMode === "simple";
  const investorMode = isInvestor(user);
  const companyOwnerMode = isCompanyOwner(user);
  const investorSimpleMode = investorMode && simpleMode;
  const investorAdvancedMode = investorMode && !simpleMode;
  const ownerSimpleMode = companyOwnerMode && simpleMode;
  const canFilterWorkers = !simpleMode && canManagePeople(user);
  useEffect(() => {
    if (simpleMode && !["newest", "oldest"].includes(sortBy)) setSortBy("newest");
  }, [simpleMode, sortBy]);
  useEffect(() => {
    if (investorSimpleMode && viewFilter !== "all") setViewFilter("all");
  }, [investorSimpleMode, viewFilter]);
  useEffect(() => {
    if (ownerSimpleMode && viewFilter !== "all") setViewFilter("all");
  }, [ownerSimpleMode, viewFilter]);
  useEffect(() => {
    if (!investorSimpleMode) return;
    if (statusFilter !== "all") setStatusFilter("all");
    if (!["newest", "oldest"].includes(sortBy)) setSortBy("newest");
  }, [investorSimpleMode, statusFilter, sortBy]);
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
      const matchesView =
        viewFilter === "all" ||
        (viewFilter === "open"
          ? item.status !== "completed"
          : viewFilter === "problems"
            ? (item.open_problem_count || 0) > 0
            : item.status === "completed");
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
  if (isCompanyWorker(user) || isIndependentContractor(user)) {
    return (
      <CompanyWorkerProjectsPage
        user={user}
        projects={projects}
        onProject={onProject}
        onCreate={isIndependentContractor(user) ? onCreate : undefined}
        uiMode={uiMode}
        onUiModeChange={onUiModeChange}
        notify={notify}
        onQueue={onQueue}
        onChanged={onChanged}
        offlineScopeKey={offlineScopeKey}
      />
    );
  }
  if (investorMode) {
    return (
      <div className="page worker-home worker-home--investor">
        <WorkerMobileHeader title="Inwestor" />
        <header className="worker-page-header">
          <div className="worker-title-row">
            <div>
              <span className="eyebrow">Moje inwestycje</span>
              <h1>{copy.title}</h1>
              <p>{copy.description}</p>
            </div>
            {canCreateProject(user) && <Button icon="plus" onClick={onCreate}>{copy.createLabel}</Button>}
          </div>
          <WorkerModeSwitch uiMode={uiMode} onUiModeChange={onUiModeChange} />
        </header>

        {investorAdvancedMode && (
          <section className="worker-filter-strip worker-filter-strip--investor" aria-label="Filtry inwestycji">
            <input type="search" placeholder={copy.searchPlaceholder} value={filter} onChange={(e) => setFilter(e.target.value)} />
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="Filtr statusu">
              <option value="all">Wszystkie statusy</option>
              <option value="assigned">Zlecone</option>
              <option value="in_progress">W realizacji</option>
              <option value="completed">Zakończone</option>
            </select>
            <select value={sortBy} onChange={(event) => setSortBy(event.target.value as typeof sortBy)} aria-label="Sortowanie">
              <option value="newest">Najnowsze</option>
              <option value="oldest">Najstarsze</option>
              <option value="start">Data rozpoczęcia</option>
              <option value="end">Data zakończenia</option>
              <option value="status">Status</option>
            </select>
          </section>
        )}

        {projects.length === 0 ? (
          <EmptyState icon="clipboard" title={copy.emptyTitle} text={copy.emptyText}>
            {canCreateProject(user) && <Button onClick={onCreate} icon="plus">{copy.createLabel}</Button>}
          </EmptyState>
        ) : visible.length === 0 ? (
          <EmptyState icon="clipboard" title="Brak wyników" text="Zmień filtr lub wyszukiwaną frazę, żeby zobaczyć pasujące inwestycje." />
        ) : (
          <section className={`worker-project-list ${simpleMode ? "worker-project-list--simple" : "worker-project-list--advanced"}`}>
            {visible.map((project) => {
              const due = formatContractDate(project.planned_end_date || project.planned_start_date) || "Termin nieustawiony";
              const startDate = formatContractDate(project.planned_start_date) || "Nie ustawiono";
              const endDate = formatContractDate(project.planned_end_date) || "Nie ustawiono";
              const activityDate = formatProjectActivityDate(project.updated_at || project.created_at) || "Brak daty";
              return (
                <article className={`worker-job-card worker-job-card--${project.status} investor-job-card`} key={project.id}>
                  <button type="button" className="worker-job-card__main" onClick={() => onProject(project)}>
                    <span className="worker-job-card__icon"><Icon name="clipboard" /></span>
                    <div className="worker-job-card__copy">
                      <div className="worker-job-card__title">
                        <h2>{project.name}</h2>
                        <span className={`status status--${project.status}`}>{statusLabels[project.status] || project.status}</span>
                      </div>
                      <p>{project.address || "Lokalizacja nieuzupełniona"}</p>
                    </div>
                    <Icon name="back" className="worker-job-card__chevron" />
                  </button>
                  <div className="worker-job-card__meta">
                    <span className="worker-job-card__stage"><Icon name="users" size={16} /> {projectPartyValue(user, project)}</span>
                    <span><Icon name="sync" size={16} /> {projectActivityLabel(project)}</span>
                    <span>{project.entry_count || 0} wpisów</span>
                    <span>{project.open_problem_count || 0} problemów</span>
                  </div>
                  {!simpleMode && (
                    <dl className="worker-job-card__details worker-job-card__details--investor">
                      <div><dt>Start</dt><dd>{startDate}</dd></div>
                      <div><dt>Koniec</dt><dd>{endDate}</dd></div>
                      <div><dt>Ost. aktywność</dt><dd>{activityDate}</dd></div>
                      <div><dt>Kwota</dt><dd>{contractAmountLabel(project) || "Nie podano"}</dd></div>
                    </dl>
                  )}
                  <div className="worker-job-card__actions investor-job-card__actions">
                    <Button type="button" variant="secondary" onClick={() => onProject(project)}>{simpleMode ? "Szczegóły" : "Podgląd"}</Button>
                    {!simpleMode && <Button type="button" variant="secondary" icon="report" onClick={() => onProject(project)}>Raporty</Button>}
                    {!simpleMode && (
                      <Button
                        type="button"
                        variant="secondary"
                        icon="link"
                        onClick={() => notify({ kind: "info", message: "Link wykonawcy utworzysz lub skopiujesz w edycji inwestycji." })}
                      >
                        Link wykonawcy
                      </Button>
                    )}
                    {!simpleMode && <Button type="button" variant="secondary" onClick={() => onProject(project)}>Edytuj</Button>}
                  </div>
                </article>
              );
            })}
          </section>
        )}
      </div>
    );
  }
  return (
    <div className={`page ${investorMode ? "investor-workspace-page investor-projects-page" : ""} ${companyOwnerMode ? "company-owner-projects-page" : ""}`}>
      <header className="page-header">
        <div><span className="eyebrow">{copy.eyebrow}</span><h1>{copy.title}</h1><p>{copy.description}</p></div>
        {investorMode || companyOwnerMode ? (
          <div className={investorMode ? "investor-page-actions" : "company-owner-page-actions"}>
            {companyOwnerMode && canCreateProject(user) && <Button icon="plus" onClick={onCreate}>{copy.createLabel}</Button>}
            <WorkerModeSwitch uiMode={uiMode} onUiModeChange={onUiModeChange} />
            {investorMode && canCreateProject(user) && <Button icon="plus" onClick={onCreate}>{copy.createLabel}</Button>}
          </div>
        ) : (
          canCreateProject(user) && <Button icon="plus" onClick={onCreate}>{copy.createLabel}</Button>
        )}
      </header>
      <section className={`panel ${investorMode ? "investor-list-panel" : ""} ${companyOwnerMode ? "company-owner-list-panel" : ""}`}>
        <div className={`project-controls ${investorMode ? `project-controls--investor project-controls--investor-${simpleMode ? "simple" : "advanced"}` : ""} ${companyOwnerMode ? `project-controls--owner project-controls--owner-${simpleMode ? "simple" : "advanced"}` : ""}`}>
          {!companyOwnerMode && !(investorSimpleMode || ownerSimpleMode) && (
            <div className="list-tabs" role="tablist" aria-label="Widok zleceń">
              <button type="button" className={viewFilter === "all" ? "active" : ""} onClick={() => setViewFilter("all")}>Wszystkie</button>
              <button type="button" className={viewFilter === "open" ? "active" : ""} onClick={() => setViewFilter("open")}>{investorMode ? "W realizacji" : "Otwarte"}</button>
              {investorMode && <button type="button" className={viewFilter === "problems" ? "active" : ""} onClick={() => setViewFilter("problems")}>Problemy</button>}
              <button type="button" className={viewFilter === "history" ? "active" : ""} onClick={() => setViewFilter("history")}>{investorMode ? "Zakończone" : "Historyczne"}</button>
            </div>
          )}
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
              <article className={`project-list-card ${investorMode ? `project-list-card--investor ${investorSimpleMode ? "project-list-card--investor-simple" : "project-list-card--investor-advanced"}` : ""} ${companyOwnerMode ? `project-list-card--owner ${ownerSimpleMode ? "project-list-card--owner-simple" : "project-list-card--owner-advanced"}` : ""}`} key={project.id}>
                <div className="project-list-card__top">
                  <span className="project-card__icon"><Icon name="clipboard" /></span>
                  <div className="project-list-card__identity">
                    <h3>{project.name}</h3>
                    <span>{investorMode ? (project.address || "Lokalizacja nieuzupełniona") : `${project.client_name || "Bez klienta"} · ${project.address || "Adres nieuzupełniony"}`}</span>
                  </div>
                  <div className="project-list-card__status">
                    <span className={`status project-status-badge status--${project.status}`}>{statusLabels[project.status] || project.status}</span>
                    <small>{projectActivityLabel(project)}</small>
                  </div>
                </div>
                {ownerSimpleMode ? (
                  <div className="company-owner-simple-chips">
                    <span>{projectStageLabel(project)}</span>
                    <span><Icon name="calendar" size={15} /> {formatContractDate(project.planned_end_date || project.planned_start_date) || "Termin nieustawiony"}</span>
                    <span><Icon name="users" size={15} /> {projectPartyValue(user, project)}</span>
                  </div>
                ) : investorSimpleMode ? (
                  <div className="investor-simple-summary">
                    <span><Icon name="users" size={17} /><b>Wykonawca</b>{projectPartyValue(user, project)}</span>
                    <span><Icon name="sync" size={17} /><b>Aktywność</b>{projectActivityLabel(project)}</span>
                  </div>
                ) : (
                  <dl className={`project-meta-grid project-meta-grid--overview ${simpleMode ? "project-meta-grid--simple" : ""}`}>
                    <div><dt>{projectPartyLabel(user)}</dt><dd>{projectPartyValue(user, project)}</dd></div>
                    <div><dt>Start</dt><dd>{formatContractDate(project.planned_start_date) || "Nie ustawiono"}</dd></div>
                    <div><dt>Koniec</dt><dd>{formatContractDate(project.planned_end_date) || "Nie ustawiono"}</dd></div>
                    <div><dt>Ostatnia aktywność</dt><dd>{formatProjectActivityDate(project.updated_at || project.created_at) || "Nie ustawiono"}</dd></div>
                    <div><dt>Kwota umowna</dt><dd>{contractAmountLabel(project) || "Nie podano"}</dd></div>
                  </dl>
                )}
                <div className="project-list-card__footer">
                  <div className="project-list-card__signals">
                    <span>{projectLastProgressLabel(project)}</span>
                    {!investorSimpleMode && <span>{project.open_problem_count || 0} problemów</span>}
                    {!investorSimpleMode && (investorMode || companyOwnerMode) && projectHasLinkOnlyWorker(project) && <span>link-only</span>}
                  </div>
                  {investorMode || companyOwnerMode ? (
                    <div className="project-list-card__actions">
                      <Button type="button" onClick={() => onProject(project)} variant="secondary">{simpleMode ? "Szczegóły" : "Podgląd"}</Button>
                      {!simpleMode && <Button type="button" onClick={() => onProject(project)} variant="secondary" icon="report">Raporty</Button>}
                      {!simpleMode && (
                        investorMode ? (
                          <Button
                            type="button"
                            onClick={() => notify({ kind: "info", message: "Link wykonawcy utworzysz lub skopiujesz w edycji inwestycji." })}
                            variant="secondary"
                            icon="link"
                          >
                            Link wykonawcy
                          </Button>
                        ) : (
                          <Button
                            type="button"
                            onClick={() => notify({ kind: "info", message: "Link klienta (/c) skopiujesz w szczegółach zlecenia." })}
                            variant="secondary"
                            icon="link"
                          >
                            Link klienta
                          </Button>
                        )
                      )}
                      {!simpleMode && companyOwnerMode && (
                        <Button
                          type="button"
                          onClick={() => notify({ kind: "info", message: "Link majstra / ekipy (/g) znajdziesz w edycji wykonawcy zlecenia." })}
                          variant="secondary"
                          icon="link"
                        >
                          Link majstra / ekipy
                        </Button>
                      )}
                      {!simpleMode && <Button type="button" onClick={() => onProject(project)} variant="secondary">Edytuj</Button>}
                    </div>
                  ) : (
                    <Button type="button" onClick={() => onProject(project)} variant="secondary">Otwórz</Button>
                  )}
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
    { icon: "camera", title: "Zdjęcia postępu", subtitle: "Zdjęcia, opis i notatka głosowa", tone: "navy", entry: { kind: "update", mode: "photo" } },
    { icon: "mic", title: "Audio", subtitle: "Nagraj ogólny opis robót", tone: "navy", entry: { kind: "update", mode: "audio" } },
    { icon: "alert", title: "Problem", subtitle: "Zgłoś przeszkodę do rozwiązania", tone: "red", entry: { kind: "problem", mode: "text" } },
  ];

  return (
    <Modal title="Dodaj postęp" onClose={onClose}>
      <div className="add-progress-sheet">
        <p>Wybierz, co chcesz dodać do tego zlecenia.</p>
      </div>
      <div className="progress-choice-grid progress-choice-grid--three">
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

function formatRecordingTime(seconds: number) {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
  const rest = Math.max(0, seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${rest}`;
}

function SelectedImageThumb({
  file,
  index,
  onRemove,
}: {
  file: File;
  index: number;
  onRemove: () => void;
}) {
  const [url, setUrl] = useState("");

  useEffect(() => {
    const nextUrl = URL.createObjectURL(file);
    setUrl(nextUrl);
    return () => URL.revokeObjectURL(nextUrl);
  }, [file]);

  return (
    <figure className="selected-photo">
      {url && <img src={url} alt={`Zdjęcie ${index + 1}`} />}
      <button type="button" onClick={onRemove} aria-label={`Usuń zdjęcie ${index + 1}`}>
        <Icon name="close" size={15} />
      </button>
    </figure>
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
  user,
  projects,
  onProject,
  onCreate,
  uiMode,
  onUiModeChange,
  notify,
  onQueue,
  onChanged,
  offlineScopeKey,
}: {
  user: User;
  projects: Project[];
  onProject: (project: Project) => void;
  onCreate?: () => void;
  uiMode: UiMode;
  onUiModeChange: (mode: UiMode) => void;
  notify: (toast: Toast) => void;
  onQueue: () => void;
  onChanged: () => void;
  offlineScopeKey?: OfflineScopeKey | null;
}) {
  const [statusFilter, setStatusFilter] = useState("all");
  const [sortBy, setSortBy] = useState<"newest" | "oldest" | "end">("newest");
  const [choiceProject, setChoiceProject] = useState<Project | null>(null);
  const [entryModal, setEntryModal] = useState<{ project: Project; entry: EntryModalState } | null>(null);
  const simpleMode = uiMode === "simple";
  const copy = projectListCopy(user);
  const independent = isIndependentContractor(user);
  const roleTitle = independent ? "Samodzielny majster" : "Majster firmy";
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
    <div className={`page worker-home ${independent ? "worker-home--independent" : ""}`}>
      <WorkerMobileHeader title={roleTitle} />
      <header className="worker-page-header">
        <div className="worker-title-row">
          <div>
            {independent && <span className="eyebrow">Typ konta: {roleTitle}</span>}
            <h1>{copy.title}</h1>
            {independent && <p>{copy.description}</p>}
          </div>
          {independent && onCreate && <Button type="button" icon="plus" onClick={onCreate}>{copy.createLabel}</Button>}
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
        <EmptyState icon="clipboard" title={copy.emptyTitle} text={copy.emptyText}>
          {independent && onCreate && <Button type="button" icon="plus" onClick={onCreate}>{copy.createLabel}</Button>}
        </EmptyState>
      ) : visible.length === 0 ? (
        <EmptyState icon="clipboard" title="Brak wyników" text="Zmień filtr, żeby zobaczyć pasujące zlecenia." />
      ) : (
        <section className={`worker-project-list ${simpleMode ? "worker-project-list--simple" : "worker-project-list--advanced"}`}>
          {visible.map((project) => {
            const stage = projectStageLabel(project);
            const due = formatContractDate(project.planned_end_date || project.planned_start_date) || "Termin nieustawiony";
            const startDate = formatContractDate(project.planned_start_date) || "Nie ustawiono";
            const endDate = formatContractDate(project.planned_end_date) || "Nie ustawiono";
            const activityDate = formatProjectActivityDate(project.updated_at || project.created_at) || "Brak daty";
            const canQuickAdd = project.status !== "completed";
            return (
              <article className={`worker-job-card worker-job-card--${project.status}`} key={project.id}>
                <button type="button" className="worker-job-card__main" onClick={() => onProject(project)}>
                  <span className="worker-job-card__icon"><Icon name="clipboard" /></span>
                  <div className="worker-job-card__copy">
                    <div className="worker-job-card__title">
                      <h2>{project.name}</h2>
                      <span className={`status status--${project.status}`}>{statusLabels[project.status] || project.status}</span>
                    </div>
                    <p>{project.client_name || "Bez klienta"} · {project.address || "Adres nieuzupełniony"}</p>
                  </div>
                  <Icon name="back" className="worker-job-card__chevron" />
                </button>
                <div className="worker-job-card__meta">
                  <span className="worker-job-card__stage">{stage}</span>
                  <span><Icon name="clipboard" size={16} /> {due}</span>
                  {!simpleMode && <span>{project.entry_count || 0} wpisów</span>}
                  {!simpleMode && <span>{project.open_problem_count || 0} problemów</span>}
                </div>
                {!simpleMode && (
                  <dl className="worker-job-card__details">
                    <div><dt>Start</dt><dd>{startDate}</dd></div>
                    <div><dt>Koniec</dt><dd>{endDate}</dd></div>
                    <div><dt>Ost. aktywność</dt><dd>{activityDate}</dd></div>
                  </dl>
                )}
                <div className="worker-job-card__actions">
                  {!simpleMode && <Button type="button" variant="secondary" onClick={() => onProject(project)}>Szczegóły</Button>}
                  {!simpleMode && canQuickAdd && (
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
          offlineScopeKey={offlineScopeKey}
        />
      )}
    </div>
  );
}

function NewEntryModal({
  project,
  kind,
  mode,
  guestToken,
  offlineScopeKey,
  onClose,
  onSaved,
  onQueued,
}: {
  project: Project;
  kind: "update" | "problem";
  mode: "photo" | "audio" | "text";
  guestToken?: string;
  offlineScopeKey?: OfflineScopeKey | null;
  onClose: () => void;
  onSaved: () => void;
  onQueued: () => void;
}) {
  const [body, setBody] = useState("");
  const [voiceNote, setVoiceNote] = useState("");
  const [stageId, setStageId] = useState(defaultEntryStageId(project));
  const [files, setFiles] = useState<File[]>([]);
  const [recordingTarget, setRecordingTarget] = useState<EntryTextTarget | null>(null);
  const [recordingStartedAt, setRecordingStartedAt] = useState<number | null>(null);
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [lastRecordingSeconds, setLastRecordingSeconds] = useState(0);
  const [speechInfo, setSpeechInfo] = useState<SpeechRecognitionInfo>({ target: null, state: "idle", message: "" });
  const [speechInterim, setSpeechInterim] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const bodyInput = useRef<HTMLTextAreaElement | null>(null);
  const chunks = useRef<Blob[]>([]);
  const speechRecognition = useRef<SpeechRecognitionInstance | null>(null);
  const speechTarget = useRef<EntryTextTarget | null>(null);
  const speechBaseText = useRef("");
  const speechFinalText = useRef("");
  const speechManualEdit = useRef(false);
  const speechStopping = useRef(false);
  const isProblemFlow = kind === "problem";
  const isPhotoFlow = !isProblemFlow && mode === "photo";
  const isAudioFlow = !isProblemFlow && mode === "audio";
  const selectedImageFiles = useMemo(
    () => files.map((file, index) => ({ file, index })).filter(({ file }) => file.type.startsWith("image/")),
    [files],
  );
  const selectedAudioFiles = files.filter((file) => file.type.startsWith("audio/"));

  function speechRecognitionConstructor(): SpeechRecognitionConstructor | null {
    const speechWindow = window as Window & {
      SpeechRecognition?: SpeechRecognitionConstructor;
      webkitSpeechRecognition?: SpeechRecognitionConstructor;
    };
    return speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition || null;
  }

  function shouldUseMobileSpeechFallback(): boolean {
    const userAgent = navigator.userAgent || "";
    const isAndroid = /Android/i.test(userAgent);
    const isChrome = /Chrome|CriOS/i.test(userAgent);
    const isMobileViewport = window.matchMedia?.("(max-width: 768px)").matches ?? false;
    return isAndroid && isChrome && isMobileViewport;
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
    if (shouldUseMobileSpeechFallback()) {
      setSpeechInfo({
        target,
        state: "unsupported",
        message: LIVE_TRANSCRIPTION_FALLBACK_MESSAGE,
      });
      return;
    }
    const Constructor = speechRecognitionConstructor();
    if (!Constructor) {
      setSpeechInfo({
        target,
        state: "unsupported",
        message: LIVE_TRANSCRIPTION_FALLBACK_MESSAGE,
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
          state: "unsupported",
          message: LIVE_TRANSCRIPTION_FALLBACK_MESSAGE,
        });
      };
      recognition.onend = () => {
        if (speechStopping.current) return;
        speechRecognition.current = null;
        setSpeechInterim("");
        setSpeechInfo({
          target,
          state: "unsupported",
          message: LIVE_TRANSCRIPTION_FALLBACK_MESSAGE,
        });
      };
      speechRecognition.current = recognition;
      recognition.start();
      setSpeechInfo({
        target,
        state: "listening",
        message: "Transkrypcja na żywo działa. Dyktowany tekst pojawi się w opisie.",
      });
    } catch {
      setSpeechInfo({
        target,
        state: "unsupported",
        message: LIVE_TRANSCRIPTION_FALLBACK_MESSAGE,
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
    if (recorder.current && recorder.current.state !== "inactive") {
      recorder.current.stop();
    }
  }, []);

  useEffect(() => {
    if (!recordingStartedAt) return;
    const tick = () => setRecordingSeconds(Math.max(0, Math.floor((Date.now() - recordingStartedAt) / 1000)));
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => window.clearInterval(timer);
  }, [recordingStartedAt]);

  useEffect(() => {
    if (mode === "text" || kind === "problem") {
      window.setTimeout(() => bodyInput.current?.focus(), 0);
    }
  }, [kind, mode]);

  async function startRecording(target: EntryTextTarget) {
    if (recordingTarget) return;
    if (!navigator.mediaDevices?.getUserMedia) {
      setError("Przeglądarka nie udostępnia nagrywania audio. Opis możesz wpisać ręcznie.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      const startedAt = Date.now();
      chunks.current = [];
      mediaRecorder.ondataavailable = (event) => chunks.current.push(event.data);
      mediaRecorder.onstop = () => {
        const blob = new Blob(chunks.current, { type: mediaRecorder.mimeType || "audio/webm" });
        if (blob.size > 0) {
          const prefix = target === "description" ? "opis" : "notatka";
          const file = new File([blob], `${prefix}-${Date.now()}.webm`, { type: blob.type });
          setFiles((current) => [...current, file]);
        }
        stream.getTracks().forEach((track) => track.stop());
        recorder.current = null;
        setRecordingTarget(null);
        setRecordingStartedAt(null);
        setLastRecordingSeconds(Math.max(1, Math.floor((Date.now() - startedAt) / 1000)));
        setRecordingSeconds(0);
      };
      recorder.current = mediaRecorder;
      mediaRecorder.start();
      setRecordingTarget(target);
      setRecordingStartedAt(startedAt);
      setRecordingSeconds(0);
      setLastRecordingSeconds(0);
      startLiveTranscription(target);
    } catch {
      setError("Przeglądarka nie udostępniła mikrofonu. Opis możesz wpisać ręcznie.");
    }
  }

  function stopRecording() {
    if (recorder.current && recorder.current.state !== "inactive") {
      recorder.current.stop();
    } else {
      setRecordingTarget(null);
      setRecordingStartedAt(null);
      setRecordingSeconds(0);
    }
    stopLiveTranscription({ keepMessage: false });
  }

  function appendFiles(fileList: FileList | null, input?: HTMLInputElement) {
    const selected = Array.from(fileList || []);
    if (selected.length > 0) {
      setFiles((current) => [...current, ...selected]);
    }
    if (input) input.value = "";
  }

  function removeFile(indexToRemove: number) {
    setFiles((current) => current.filter((_, index) => index !== indexToRemove));
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
    if (recordingTarget) {
      setError("Zatrzymaj nagrywanie przed zapisaniem wpisu.");
      return;
    }
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
      if ((!navigator.onLine || reason instanceof TypeError) && offlineScopeKey) {
        const queued: QueuedEntry = {
          id: clientRef,
          scopeKey: offlineScopeKey,
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
      if (!navigator.onLine || reason instanceof TypeError) {
        setError("Nie udało się zapisać offline w tej sesji. Odśwież widok i spróbuj ponownie.");
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

  const modalCopy = kind === "problem"
    ? {
      title: "Zgłoś problem",
      eyebrow: "Problem",
      heading: "Zgłoś problem do rozwiązania",
      description: "Opisz przeszkodę, dodaj zdjęcia albo nagraj krótką notatkę. Problem zostanie zapisany w historii zlecenia.",
    }
    : mode === "photo"
      ? {
        title: "Zdjęcia postępu",
        eyebrow: "Zdjęcia etapu",
        heading: "Dodaj zdjęcia postępu",
        description: "Dodaj zdjęcia, wpisz opis albo nagraj krótką notatkę głosową. Transkrypcję możesz poprawić przed zapisem.",
      }
      : mode === "audio"
        ? {
          title: "Audio",
          eyebrow: "Audio",
          heading: "Opis głosowy robót",
          description: "Nagraj ogólny opis wykonanych prac. To audio nie musi być przypisane do konkretnego zdjęcia.",
        }
        : {
          title: "Opis",
          eyebrow: "Opis",
          heading: "Opisz wykonane prace",
          description: "Wpisz krótką notatkę. Zdjęcia i audio możesz dodać jako załączniki.",
        };

  const photoActions = (
    <div className="entry-photo-actions">
      <label>
        <Icon name="camera" size={24} />
        <strong>Aparat</strong>
        <input
          type="file"
          accept="image/*"
          capture="environment"
          multiple
          onChange={(event) => appendFiles(event.currentTarget.files, event.currentTarget)}
        />
      </label>
      <label>
        <Icon name="image" size={24} />
        <strong>Galeria</strong>
        <input
          type="file"
          accept="image/*"
          multiple
          onChange={(event) => appendFiles(event.currentTarget.files, event.currentTarget)}
        />
      </label>
    </div>
  );

  const photoPreview = selectedImageFiles.length > 0 ? (
    <div className="selected-photo-grid">
      {selectedImageFiles.map(({ file, index }) => (
        <SelectedImageThumb
          key={`${file.name}-${file.lastModified}-${index}`}
          file={file}
          index={index}
          onRemove={() => removeFile(index)}
        />
      ))}
    </div>
  ) : (
    <div className="entry-empty-media">
      <Icon name="camera" size={28} />
      <span>Dodane zdjęcia pojawią się tutaj.</span>
    </div>
  );

  function recorderSection(target: EntryTextTarget, hero = false) {
    const active = recordingTarget === target;
    const time = active ? recordingSeconds : lastRecordingSeconds;
    return (
    <>
      <div className={`recorder ${active ? "recorder--active" : ""} ${hero ? "recorder--hero" : ""}`}>
        <button
          type="button"
          onClick={active ? stopRecording : () => startRecording(target)}
          disabled={Boolean(recordingTarget && !active)}
          aria-label={active ? "Zatrzymaj nagrywanie" : "Rozpocznij nagrywanie"}
        >
          <Icon name="mic" size={30} />
        </button>
        <div>
          <strong>{active ? `Nagrywanie... ${formatRecordingTime(time)}` : selectedAudioFiles.length ? `Nagranie dodane (${selectedAudioFiles.length})` : "Stuknij, aby nagrać"}</strong>
          <span>Nagranie audio zostanie zapisane. Jeśli transkrypcja na żywo będzie dostępna, tekst pojawi się w polu opisu.</span>
        </div>
      </div>
      {renderSpeechStatus(target)}
    </>
    );
  }

  const descriptionLabel = isProblemFlow ? "Opis problemu" : isAudioFlow ? "Krótki tytuł / notatka (opcjonalnie)" : "Opis etapu";
  const descriptionPlaceholder = isProblemFlow
    ? "Opisz problem i podaj szczegóły, które pomogą w jego rozwiązaniu..."
    : isAudioFlow
      ? "Np. Prace wykończeniowe w łazience"
      : "Np. Zamontowano szafki i przygotowano ścianę pod płytki...";

  const descriptionSection = (
    <label>
      {descriptionLabel}
      <textarea
        ref={bodyInput}
        rows={isAudioFlow ? 4 : 5}
        maxLength={isAudioFlow ? 140 : 500}
        value={body}
        onChange={(e) => { markManualTextEdit("description"); setBody(e.target.value); }}
        placeholder={descriptionPlaceholder}
      />
    </label>
  );

  const submitLabel = navigator.onLine
    ? isProblemFlow
      ? "Dodaj problem"
      : isAudioFlow
        ? "Zapisz opis audio"
        : "Zapisz postęp"
    : "Zapisz do wysłania";

  return (
    <Modal title={modalCopy.title} onClose={onClose}>
      <form className={`form-stack entry-progress-flow entry-progress-flow--${isProblemFlow ? "problem" : mode}`} onSubmit={submit}>
        {project.stages && project.stages.length > 0 && (
          <label>Etap<select value={stageId} onChange={(e) => setStageId(e.target.value)}><option value="">Bez etapu</option>{project.stages.map((stage) => <option value={stage.id} key={stage.id}>{stage.title}</option>)}</select></label>
        )}
        <div className={`entry-modal-context entry-modal-context--${kind === "problem" ? "problem" : mode}`}>
          <span>{modalCopy.eyebrow}</span>
          <strong>{modalCopy.heading}</strong>
          <p>{modalCopy.description}</p>
        </div>
        {isPhotoFlow && (
          <>
            <section className="entry-flow-section">
              <h3>Dodaj zdjęcia</h3>
              {photoActions}
              {photoPreview}
            </section>
            {descriptionSection}
            <section className="entry-flow-section entry-flow-section--voice">
              <h3>Notatka głosowa <span>(opcjonalnie)</span></h3>
              {recorderSection("description")}
              <p>Nagranie audio zostanie zapisane. Tekst możesz dodać ręcznie, jeśli transkrypcja na żywo nie będzie dostępna.</p>
            </section>
          </>
        )}
        {isAudioFlow && (
          <>
            <section className="entry-audio-hero">
              {recorderSection("description", true)}
            </section>
            {descriptionSection}
          </>
        )}
        {isProblemFlow && (
          <>
            <div className="problem-flow-note">
              <span>Do rozwiązania</span>
              <p>Problem zapisze się jako otwarty. Dodaj tyle kontekstu, ile potrzeba do decyzji lub naprawy.</p>
            </div>
            {descriptionSection}
            <section className="entry-flow-section">
              <h3>Zdjęcia <span>(opcjonalnie)</span></h3>
              {photoActions}
              {photoPreview}
            </section>
            <section className="entry-flow-section entry-flow-section--voice">
              <h3>Notatka głosowa <span>(opcjonalnie)</span></h3>
              {recorderSection("description")}
            </section>
          </>
        )}
        {!isPhotoFlow && !isAudioFlow && !isProblemFlow && (
          <>
            {descriptionSection}
            <section className="entry-flow-section">
              <h3>Zdjęcia</h3>
              {photoActions}
              {photoPreview}
            </section>
            {recorderSection("description")}
          </>
        )}
        {selectedAudioFiles.length > 0 && (
          <div className="file-chips">
            {selectedAudioFiles.map((file, index) => (
              <span key={`${file.name}-${index}`}>
                Nagranie {index + 1}
                <button type="button" onClick={() => removeFile(files.indexOf(file))}>×</button>
              </span>
            ))}
          </div>
        )}
        {error && <p className="form-error">{error}</p>}
        <Button type="submit" busy={busy} icon={kind === "problem" ? "alert" : "check"}>{submitLabel}</Button>
      </form>
    </Modal>
  );
}

function TimelineEntry({
  item,
  guestToken,
  onRefresh,
  canDelete,
  onDelete,
}: {
  item: Entry;
  guestToken?: string;
  onRefresh: () => void;
  canDelete?: boolean;
  onDelete?: (entry: Entry) => void;
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
        {canDelete && (
          <button type="button" className="entry-delete-button" onClick={() => onDelete?.(item)}>
            Usuń dokumentację
          </button>
        )}
        {item.stage && <span className="stage-label">{item.stage.title}</span>}
        {(item.body || item.transcript) && <p>{item.body || item.transcript}</p>}
        {item.transcript && item.body && <details><summary>Transkrypcja głosu</summary><p>{item.transcript}</p></details>}
        {item.media.some((asset) => asset.kind === "image") && <div className="media-grid">{item.media.filter((asset) => asset.kind === "image").map((asset) => <a href={guestToken ? `${asset.url}?guest_token=${encodeURIComponent(guestToken)}` : asset.url} target="_blank" key={asset.id}><img src={guestToken ? `${asset.url}?guest_token=${encodeURIComponent(guestToken)}` : asset.url} alt={asset.original_name} loading="lazy" /></a>)}</div>}
        {item.media.filter((asset) => asset.kind === "audio").map((asset) => <audio controls src={guestToken ? `${asset.url}?guest_token=${encodeURIComponent(guestToken)}` : asset.url} key={asset.id} />)}
        {item.kind === "problem" && <button className={`problem-toggle problem-toggle--${item.problem_status}`} onClick={toggleProblem}><Icon name="check" size={16} /> {item.problem_status === "resolved" ? "Problem rozwiązany" : "Oznacz jako rozwiązany"}</button>}
        <button className="comment-toggle" onClick={() => setOpen(!open)}>{item.comments.length} komentarzy · {open ? "Ukryj" : "Otwórz"}</button>
        {open && <div className="comments">{item.comments.map((entryComment) => <div key={entryComment.id}><strong>{commentAuthorLabel(entryComment)}</strong>{entryComment.intent && entryComment.intent !== "comment" && <small>{commentIntentLabel(entryComment)}</small>}<p>{entryComment.body}</p></div>)}<form onSubmit={addComment}><input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Dodaj komentarz..." /><Button type="submit" variant="secondary">Wyślij</Button></form></div>}
      </div>
    </article>
  );
}

function WorkerEntryDetailsModal({
  entry,
  onClose,
  guestToken,
  onRefresh,
}: {
  entry: Entry;
  onClose: () => void;
  guestToken?: string;
  onRefresh?: () => void;
}) {
  const images = entry.media.filter((asset) => asset.kind === "image");
  const audio = entry.media.filter((asset) => asset.kind === "audio");
  const title = entry.kind === "problem" ? "Problem" : audio.length ? "Audio" : images.length ? "Dokumentacja" : "Opis";
  const author = entry.author?.name || entry.author?.email || entry.guest_label || "Nieznany autor";
  const mediaUrl = (url: string) => guestToken ? `${url}?guest_token=${encodeURIComponent(guestToken)}` : url;
  const canEditProblem = entry.kind === "problem" && !guestToken;
  const [editProblem, setEditProblem] = useState(false);
  const [problemBody, setProblemBody] = useState(entry.body);
  const [problemBusy, setProblemBusy] = useState(false);
  const [problemError, setProblemError] = useState("");

  async function saveProblem(nextStatus?: "open" | "resolved") {
    if (!canEditProblem) return;
    setProblemBusy(true);
    setProblemError("");
    try {
      await api(`/entries/${entry.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          body: problemBody,
          ...(nextStatus ? { problem_status: nextStatus } : {}),
        }),
      });
      onRefresh?.();
      onClose();
    } catch (reason) {
      setProblemError(reason instanceof Error ? reason.message : "Nie udało się zapisać problemu");
    } finally {
      setProblemBusy(false);
    }
  }

  return (
    <Modal title="Szczegóły wpisu" onClose={onClose} wide>
      <div className="worker-entry-details">
        <section className="worker-entry-details__summary">
          <span><Icon name={entry.kind === "problem" ? "alert" : audio.length ? "mic" : images.length ? "camera" : "clipboard"} /></span>
          <div>
            <small>Typ wpisu</small>
            <h3>{title}</h3>
            <p>{new Intl.DateTimeFormat("pl", { dateStyle: "medium", timeStyle: "short" }).format(new Date(entry.occurred_at))} · {author}</p>
            {entry.stage && <span className="stage-label">{entry.stage.title}</span>}
            {entry.kind === "problem" && entry.problem_status && (
              <span className={`status status--${entry.problem_status === "resolved" ? "completed" : "in_progress"}`}>
                {entry.problem_status === "resolved" ? "Problem rozwiązany" : "Problem otwarty"}
              </span>
            )}
          </div>
        </section>

        {canEditProblem && (
          <section className="worker-entry-details__problem-actions">
            <h4>Obsługa problemu</h4>
            {editProblem ? (
              <div className="worker-problem-editor">
                <textarea value={problemBody} onChange={(event) => setProblemBody(event.target.value)} rows={4} />
                {problemError && <p className="form-error">{problemError}</p>}
                <div>
                  <Button type="button" variant="secondary" onClick={() => setEditProblem(false)}>Anuluj</Button>
                  <Button type="button" busy={problemBusy} onClick={() => void saveProblem()}>Zapisz opis problemu</Button>
                </div>
              </div>
            ) : (
              <div className="worker-problem-actions">
                <Button type="button" variant="secondary" onClick={() => setEditProblem(true)}>Edytuj problem</Button>
                {entry.problem_status === "resolved" ? (
                  <Button type="button" variant="secondary" busy={problemBusy} onClick={() => void saveProblem("open")}>Otwórz ponownie problem</Button>
                ) : (
                  <Button type="button" variant="success" busy={problemBusy} onClick={() => void saveProblem("resolved")}>Oznacz jako rozwiązany</Button>
                )}
              </div>
            )}
          </section>
        )}

        {entry.body && (
          <section>
            <h4>Opis</h4>
            <p>{entry.body}</p>
          </section>
        )}

        {entry.transcript && (
          <section>
            <h4>Transkrypcja audio</h4>
            <p>{entry.transcript}</p>
          </section>
        )}

        {images.length > 0 && (
          <section>
            <h4>Zdjęcia</h4>
            <div className="worker-entry-details__media-grid">
              {images.map((asset) => (
                <a href={mediaUrl(asset.url)} target="_blank" rel="noreferrer" key={asset.id}>
                  <img src={mediaUrl(asset.url)} alt={asset.original_name} loading="lazy" />
                </a>
              ))}
            </div>
          </section>
        )}

        {audio.length > 0 && (
          <section>
            <h4>Audio</h4>
            <div className="worker-entry-details__audio-list">
              {audio.map((asset) => (
                <div key={asset.id}>
                  <strong>{asset.original_name || "Nagranie audio"}</strong>
                  <audio controls src={mediaUrl(asset.url)} />
                </div>
              ))}
            </div>
          </section>
        )}

        {entry.comments.length > 0 && (
          <section>
            <h4>Komentarze</h4>
            <div className="worker-entry-details__comments">
              {entry.comments.map((comment) => (
                <article key={comment.id}>
                  <strong>{commentAuthorLabel(comment)}</strong>
                  {comment.intent && comment.intent !== "comment" && <em>{commentIntentLabel(comment)}</em>}
                  <small>{new Intl.DateTimeFormat("pl", { dateStyle: "short", timeStyle: "short" }).format(new Date(comment.created_at))}</small>
                  <p>{comment.body}</p>
                </article>
              ))}
            </div>
          </section>
        )}

        {!entry.body && !entry.transcript && images.length === 0 && audio.length === 0 && entry.comments.length === 0 && (
          <p className="form-note">Ten wpis nie ma dodatkowych danych do pokazania.</p>
        )}
      </div>
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

async function fetchPdfBlob(pdfHref: string, guestToken?: string): Promise<Blob> {
  const headers = new Headers();
  if (guestToken) headers.set("x-guest-token", guestToken);
  const requestUrl = pdfHref.startsWith("/api/") ? pdfHref : `/api${pdfHref}`;
  const response = await fetch(requestUrl, {
    credentials: "include",
    headers,
  });
  if (!response.ok) {
    let detail = response.status >= 500
      ? "Nie udało się otworzyć raportu PDF"
      : response.statusText || "Nie udało się otworzyć raportu PDF";
    if (response.status < 500) {
      try {
        const payload = await response.json();
        detail = typeof payload?.detail === "string" ? payload.detail : detail;
      } catch {
        // The response can be an upstream HTML page. Keep a report-specific error.
      }
    }
    throw new ApiError(response.status, detail);
  }
  return response.blob();
}

function openBlobUrl(blob: Blob, filename: string, download: boolean) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.rel = "noreferrer";
  if (download) {
    link.download = filename;
  } else {
    link.target = "_blank";
  }
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 30000);
}

function GeneratedReportsPanel({
  projectId,
  reports,
  guestToken,
  onRefresh,
  notify,
  generatedReportLimit,
  copy,
  loading = false,
  error = "",
}: {
  projectId: string;
  reports: Report[];
  guestToken?: string;
  onRefresh: () => Promise<void> | void;
  notify: (toast: Toast) => void;
  generatedReportLimit?: number;
  copy?: {
    title?: string;
    description?: string;
    finalDescription?: string;
  };
  loading?: boolean;
  error?: string;
}) {
  const [busyType, setBusyType] = useState<"daily" | "final" | null>(null);
  const [busyReportAction, setBusyReportAction] = useState<string | null>(null);
  const [dailyDate, setDailyDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [showAllGeneratedReports, setShowAllGeneratedReports] = useState(false);
  const generatingRef = useRef(false);
  const isGeneratingReport = busyType !== null;
  const generatedReports = reports.filter(
    (report) => ["daily", "final"].includes(report.report_type) && report.pdf_url,
  );
  const hasGeneratedReportLimit = Boolean(generatedReportLimit && generatedReportLimit > 0);
  const orderedGeneratedReports = hasGeneratedReportLimit
    ? [...generatedReports].sort((first, second) => {
      const firstTime = new Date(first.report_date || first.published_at || first.created_at).getTime();
      const secondTime = new Date(second.report_date || second.published_at || second.created_at).getTime();
      return (Number.isFinite(secondTime) ? secondTime : 0) - (Number.isFinite(firstTime) ? firstTime : 0);
    })
    : generatedReports;
  const visibleGeneratedReports = hasGeneratedReportLimit && !showAllGeneratedReports
    ? orderedGeneratedReports.slice(0, generatedReportLimit)
    : orderedGeneratedReports;

  useEffect(() => {
    generatingRef.current = false;
    setBusyType(null);
    setBusyReportAction(null);
    setShowAllGeneratedReports(false);
  }, [projectId]);

  async function generateReport(type: "daily" | "final") {
    if (generatingRef.current) return;
    generatingRef.current = true;
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
      generatingRef.current = false;
      setBusyType(null);
    }
  }

  async function handleReportPdf(report: Report, action: "open" | "download") {
    const pdfHref = reportPdfHref(report, guestToken);
    if (!pdfHref) {
      notify({ kind: "error", message: "Raport PDF nie jest jeszcze gotowy" });
      return;
    }
    setBusyReportAction(`${report.id}:${action}`);
    try {
      const blob = await fetchPdfBlob(pdfHref, guestToken);
      openBlobUrl(blob, `${reportTypeLabel(report)}-${reportDisplayDate(report)}.pdf`, action === "download");
    } catch (reason) {
      notify({
        kind: "error",
        message: reason instanceof Error ? reason.message : "Nie udało się otworzyć raportu PDF",
      });
    } finally {
      setBusyReportAction(null);
    }
  }

  return (
    <section className="project-pdf-panel panel">
      <div className="panel__header">
        <div>
          <h2>{copy?.title || "Raporty PDF"}</h2>
          <p>{copy?.description || "Generuj raporty bez automatycznego otwierania PDF-a. Gotowe pliki znajdziesz na liście poniżej."}</p>
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
              <input
                type="date"
                value={dailyDate}
                disabled={isGeneratingReport || loading}
                onChange={(event) => setDailyDate(event.target.value)}
              />
            </label>
            <Button
              variant="secondary"
              icon="report"
              busy={busyType === "daily"}
              disabled={isGeneratingReport || loading}
              onClick={() => void generateReport("daily")}
            >
              Wygeneruj dzienny raport PDF
            </Button>
          </article>
          <article>
            <div>
              <h3>Raport końcowy</h3>
              <p>{copy?.finalDescription || "Pełne podsumowanie zlecenia i historii prac."}</p>
            </div>
            <Button
              icon="report"
              busy={busyType === "final"}
              disabled={isGeneratingReport || loading}
              onClick={() => void generateReport("final")}
            >
              Wygeneruj końcowy raport PDF
            </Button>
          </article>
        </div>

        <div className="generated-report-list">
          <h3>Wygenerowane raporty</h3>
          {error && <p className="form-error">{error}</p>}
          {generatedReports.length === 0 ? (
            <p className="empty-note">Brak wygenerowanych raportów. Wygeneruj raport dzienny albo końcowy.</p>
          ) : (
            <>
            {visibleGeneratedReports.map((report) => {
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
                    {pdfHref && (
                      <Button
                        type="button"
                        variant="secondary"
                        busy={busyReportAction === `${report.id}:open`}
                        onClick={() => void handleReportPdf(report, "open")}
                      >
                        Otwórz
                      </Button>
                    )}
                    {pdfHref && (
                      <Button
                        type="button"
                        busy={busyReportAction === `${report.id}:download`}
                        onClick={() => void handleReportPdf(report, "download")}
                      >
                        Pobierz
                      </Button>
                    )}
                  </div>
                </article>
              );
            })}
            {hasGeneratedReportLimit && generatedReports.length > (generatedReportLimit || 0) && (
              <button
                type="button"
                className="generated-report-list__toggle"
                onClick={() => setShowAllGeneratedReports((current) => !current)}
              >
                {showAllGeneratedReports ? "Zwiń" : `Pokaż wszystkie (${generatedReports.length})`}
              </button>
            )}
            </>
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
  onProjectChanged,
  offlineScopeKey,
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
  onProjectChanged?: () => void;
  offlineScopeKey?: OfflineScopeKey | null;
}) {
  const [project, setProject] = useState<Project | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [reportsLoading, setReportsLoading] = useState(false);
  const [reportError, setReportError] = useState("");
  const [clientLink, setClientLink] = useState<ClientLink | null>(null);
  const [loading, setLoading] = useState(true);
  const [entryModal, setEntryModal] = useState<EntryModalState | null>(null);
  const [showAddProgressChoice, setShowAddProgressChoice] = useState(false);
  const [showReports, setShowReports] = useState(false);
  const [showManage, setShowManage] = useState(false);
  const [showClientLink, setShowClientLink] = useState(false);
  const [showClientCoverPicker, setShowClientCoverPicker] = useState(false);
  const [showWorkerStagePicker, setShowWorkerStagePicker] = useState(false);
  const [selectedWorkerEntry, setSelectedWorkerEntry] = useState<Entry | null>(null);
  const [deleteEntryTarget, setDeleteEntryTarget] = useState<Entry | null>(null);
  const [busyStageId, setBusyStageId] = useState<string | undefined>();
  const [coverBusy, setCoverBusy] = useState(false);
  const workerReportsRef = useRef<HTMLDivElement | null>(null);
  const isInvestorPanelUser = Boolean(user && isInvestor(user) && !guestToken);
  const fieldMode = Boolean(guestToken) || (uiMode !== "advanced" && !isInvestorPanelUser);

  const loadReports = useCallback(async (targetProject: Project | null = project) => {
    if (!targetProject) {
      setReports([]);
      setReportError("");
      setReportsLoading(false);
      return;
    }
    const canLoadReports = guestToken
      ? targetProject.guest && ["history", "view"].includes(targetProject.guest.permission)
      : true;
    if (!canLoadReports) {
      setReports([]);
      setReportError("");
      setReportsLoading(false);
      return;
    }
    setReportsLoading(true);
    setReportError("");
    try {
      const reportData = await api<Report[]>(`/projects/${targetProject.id}/reports`, {}, guestToken);
      setReports(reportData);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Nie udało się wczytać raportów PDF";
      setReports([]);
      setReportError(message);
      notify({ kind: "error", message });
    } finally {
      setReportsLoading(false);
    }
  }, [guestToken, notify, project]);

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
        try {
          const reportData = await api<Report[]>(`/projects/${projectId}/reports`, {}, guestToken);
          setReports(reportData);
          setReportError("");
        } catch (reason) {
          setReports([]);
          setReportError(reason instanceof Error ? reason.message : "Nie udało się wczytać raportów PDF");
          notify({
            kind: "error",
            message: reason instanceof Error ? reason.message : "Nie udało się wczytać raportów PDF",
          });
        }
      } else {
        setReports([]);
      }
      if (!guestToken) {
        try {
          const linkData = await api<ClientLink>(`/projects/${projectId}/client-link`);
          setClientLink(linkData);
        } catch {
          setClientLink(null);
        }
      }
    } catch (reason) {
      setProject(null);
      setEntries([]);
      setReports([]);
      setReportError("");
      setReportsLoading(false);
      setClientLink(null);
      if (!guestToken && reason instanceof ApiError && [403, 404].includes(reason.status)) {
        notify({ kind: "info", message: "Nie masz dostępu do tego zlecenia w tej sesji. Wracam do listy." });
        onProjectChanged?.();
        onUnavailable?.();
        return;
      }
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się otworzyć projektu" });
    } finally {
      setLoading(false);
    }
  }, [guestToken, notify, onProjectChanged, onUnavailable, projectId]);

  const refreshAfterProjectMutation = useCallback(async () => {
    onProjectChanged?.();
    await load();
  }, [load, onProjectChanged]);

  useEffect(() => {
    setProject(null);
    setEntries([]);
    setReports([]);
    setReportError("");
    setReportsLoading(false);
    setClientLink(null);
    setShowReports(false);
    setShowManage(false);
    setShowClientLink(false);
    setShowClientCoverPicker(false);
    setShowAddProgressChoice(false);
    setShowWorkerStagePicker(false);
    setSelectedWorkerEntry(null);
    setDeleteEntryTarget(null);
    setLoading(true);
  }, [guestToken, projectId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!reports.some((report) => report.status === "generating")) return;
    const timer = setInterval(() => {
      void loadReports(project);
    }, 4000);
    return () => clearInterval(timer);
  }, [loadReports, project, reports]);

  if (loading) return <div className="page"><div className="loading-screen"><span className="spinner" /> Ładowanie projektu...</div></div>;
  if (!project) {
    return (
      <div className="page">
        <EmptyState icon="alert" title="Nie udało się otworzyć projektu" text="Link może być nieaktywny albo nie masz dostępu.">
          <Button type="button" variant="secondary" onClick={onBack}>Wróć do zleceń</Button>
        </EmptyState>
      </div>
    );
  }

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
      await refreshAfterProjectMutation();
      notify({ kind: "success", message: "Zlecenie zostało zamknięte." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zamknąć zlecenia" });
    }
  }

  async function startProject() {
    try {
      await api(`/projects/${projectIdForStatusActions}/start`, { method: "POST", body: JSON.stringify({}) });
      await refreshAfterProjectMutation();
      notify({ kind: "success", message: "Zlecenie jest w realizacji." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się rozpocząć roboty" });
    }
  }

  async function reopenProject() {
    if (!window.confirm("Czy chcesz ponownie otworzyć zlecenie? Status wróci do W realizacji.")) return;
    try {
      await api(`/projects/${projectIdForStatusActions}/reopen`, { method: "POST", body: JSON.stringify({}) });
      await refreshAfterProjectMutation();
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
      await refreshAfterProjectMutation();
      notify({ kind: "success", message: "Etap zlecenia zaktualizowany." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zmienić etapu" });
    } finally {
      setBusyStageId(undefined);
    }
  }

  function canDeleteEntry(entry: Entry) {
    if (!user || guestToken) return false;
    if (["owner", "manager"].includes(project?.role || "")) return true;
    return entry.author?.id === user.id;
  }

  async function deleteDocumentationEntry() {
    if (!deleteEntryTarget) return;
    try {
      await api(`/entries/${deleteEntryTarget.id}`, { method: "DELETE" });
      setSelectedWorkerEntry((current) => current?.id === deleteEntryTarget.id ? null : current);
      setDeleteEntryTarget(null);
      await refreshAfterProjectMutation();
      notify({ kind: "success", message: "Dokumentacja usunięta" });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się usunąć dokumentacji" });
    }
  }

  function showAndScrollWorkerReports() {
    setShowReports(true);
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        workerReportsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
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
  const isCompanyWorkerFieldUser = Boolean(user && isCompanyWorker(user) && !guestToken);
  const isIndependentFieldUser = Boolean(user && isIndependentContractor(user) && !guestToken);
  const isRoleFieldUser = isCompanyWorkerFieldUser || isIndependentFieldUser;
  const fieldRoleTitle = isIndependentFieldUser ? "Samodzielny majster" : "Majster firmy";
  const recentEntries = entries.slice(0, 3);
  const workerHistoryEntries = uiMode === "advanced" ? entries.slice(0, 8) : recentEntries;
  const workerProblemCount = entries.filter((entry) => entry.kind === "problem").length;
  const workerImageCount = entries.reduce((sum, entry) => sum + entry.media.filter((asset) => asset.kind === "image").length, 0);
  const workerAudioCount = entries.reduce((sum, entry) => sum + entry.media.filter((asset) => asset.kind === "audio").length, 0);
  const canStartWorkerProject = canAdd && project.status === "assigned";
  const canAddWorkerProgress = canAdd && project.status === "in_progress";
  const canFinishWorkerProject = canCloseProject && project.status === "in_progress";
  const canReopenWorkerProject = project.status === "completed" && (isIndependentFieldUser ? canReopenProject : canAdd);
  const projectImages = entries.flatMap((entry) =>
    entry.media
      .filter((asset) => asset.kind === "image")
      .map((asset) => ({ asset, entry })),
  );
  const selectedClientCover = projectImages.find(({ asset }) => asset.id === project.client_cover_media_id)?.asset || null;
  const fallbackClientCover = projectImages[0]?.asset || null;
  const visibleClientCover = selectedClientCover || fallbackClientCover;
  const canManageClientCover = Boolean(!guestToken && ["owner", "manager"].includes(project.role || ""));

  async function updateClientCover(mediaId: string | null) {
    setCoverBusy(true);
    try {
      const updated = await api<Project>(`/projects/${projectIdForStatusActions}/client-cover`, {
        method: "PATCH",
        body: JSON.stringify({ media_id: mediaId }),
      });
      setProject(updated);
      await refreshAfterProjectMutation();
      setShowClientCoverPicker(false);
      notify({ kind: "success", message: mediaId ? "Zdjęcie główne linku klienta zapisane." : "Wybór zdjęcia głównego wyczyszczony." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zapisać zdjęcia głównego" });
    } finally {
      setCoverBusy(false);
    }
  }

  const clientCoverCard = canManageClientCover ? (
    <section className="worker-detail-card client-cover-card">
      <div className="client-cover-card__content">
        <div>
          <h2>Zdjęcie główne linku klienta</h2>
          <p>{selectedClientCover ? "Wybrane zdjęcie będzie pierwszym obrazem w publicznym podglądzie klienta." : visibleClientCover ? "Klient zobaczy najnowsze zdjęcie z historii. Możesz wybrać inne." : "Dodaj zdjęcie do historii, żeby ustawić obraz główny."}</p>
        </div>
        {visibleClientCover ? (
          <button type="button" className="client-cover-preview" onClick={() => setShowClientCoverPicker(true)}>
            <img src={visibleClientCover.url} alt={visibleClientCover.original_name || "Zdjęcie główne"} />
            <span>{selectedClientCover ? "Wybrane" : "Fallback"}</span>
          </button>
        ) : (
          <div className="client-cover-empty"><Icon name="camera" /><span>Brak zdjęć</span></div>
        )}
      </div>
      <div className="client-cover-card__actions">
        <Button type="button" variant="secondary" icon="camera" disabled={projectImages.length === 0} onClick={() => setShowClientCoverPicker(true)}>
          Wybierz zdjęcie
        </Button>
        {selectedClientCover && (
          <Button type="button" variant="secondary" disabled={coverBusy} onClick={() => void updateClientCover(null)}>
            Wyczyść wybór
          </Button>
        )}
      </div>
    </section>
  ) : null;

  if (isInvestorPanelUser) {
    const mode = uiMode || "simple";
    const advancedMode = mode === "advanced";
    const contractorName = projectPartyValue(user!, project);
    const openProblems = entries.filter((entry) => entry.kind === "problem" && entry.problem_status !== "resolved").length;
    const activityDate = formatProjectActivityDate(project.updated_at || project.created_at) || "Brak daty";
    const historyEntries = advancedMode ? entries.slice(0, 10) : entries.slice(0, 4);
    const completedOrReopenAction = project.status === "completed" && canReopenProject;

    const investorActions = (
      <section className="investor-detail-actions" aria-label="Akcje inwestycji">
        {canAdd && <Button type="button" variant="secondary" icon="plus" onClick={() => setShowAddProgressChoice(true)}>Dodaj wpis</Button>}
        {canGeneratePdfReports && <Button type="button" variant="secondary" icon="report" onClick={showAndScrollWorkerReports}>Raport PDF</Button>}
        <Button
          type="button"
          variant="secondary"
          icon="link"
          onClick={() => {
            if (project.can_edit_details) {
              setShowManage(true);
              notify({ kind: "info", message: "Link wykonawcy znajdziesz w sekcji wykonawcy edycji inwestycji." });
            } else {
              notify({ kind: "info", message: "Link wykonawcy jest dostępny tylko dla osoby zarządzającej inwestycją." });
            }
          }}
        >
          Link wykonawcy
        </Button>
        {project.can_edit_details && <Button type="button" variant="secondary" icon="settings" onClick={() => setShowManage(true)}>Edytuj inwestycję</Button>}
        {completedOrReopenAction && <Button type="button" variant="success" onClick={reopenProject}>Otwórz ponownie</Button>}
      </section>
    );

    const summaryCard = (
      <section className="worker-detail-card investor-summary-card">
        <div className="investor-summary-card__progress">
          <small>Postęp całkowity</small>
          <strong>{progress}%</strong>
          <span>{completedCount} z {project.stages?.length || 0} etapów ukończonych</span>
          <div className="progress"><i style={{ width: `${progress}%` }} /></div>
        </div>
        <div className="investor-summary-card__facts">
          <div><span><Icon name="users" /></span><small>Wykonawca</small><strong>{contractorName}</strong></div>
          <div><span><Icon name="clock" /></span><small>Ostatnia aktywność</small><strong>{activityDate}</strong></div>
          <div><span><Icon name="check" /></span><small>Otwarte problemy</small><strong>{openProblems} {openProblems === 1 ? "problem" : "problemów"}</strong></div>
        </div>
      </section>
    );

    const investorHistory = (
      <section className="worker-detail-card investor-history-card">
        <div className="worker-section-heading investor-history-card__heading">
          <div>
            <h2>Historia postępu inwestycji</h2>
            <p>Wpisy, zdjęcia, audio i komentarze z realizacji.</p>
          </div>
          {canAdd && (
            <div className="investor-history-card__actions">
              <Button type="button" variant="secondary" icon="plus" onClick={() => setShowAddProgressChoice(true)}>Dodaj wpis</Button>
              <Button type="button" variant="secondary" className="problem" icon="alert" onClick={() => setEntryModal({ kind: "problem", mode: "text" })}>Zgłoś problem</Button>
            </div>
          )}
        </div>
        {historyEntries.length === 0 ? (
          <div className="worker-empty-history">
            <Icon name="camera" />
            <strong>Tu powstanie historia inwestycji</strong>
            <p>Dodaj pierwszy wpis: zdjęcia, opis, audio albo problem.</p>
          </div>
        ) : (
          <div className="investor-history-list">
            {historyEntries.map((entry) => {
              const images = entry.media.filter((asset) => asset.kind === "image");
              const audio = entry.media.filter((asset) => asset.kind === "audio");
              const visibleImages = images.slice(0, advancedMode ? 4 : 3);
              const extraImages = Math.max(0, images.length - visibleImages.length);
              const title = entry.kind === "problem"
                ? "Zgłoszono problem"
                : audio.length
                  ? "Dodano dokumentację"
                  : images.length
                    ? "Dodano aktualizację"
                    : entry.stage?.status === "completed"
                      ? "Etap zakończony"
                      : "Dodano aktualizację";
              return (
                <article className={`investor-history-item investor-history-item--${entry.kind}`} key={entry.id}>
                  <button type="button" className="investor-history-item__body" onClick={() => setSelectedWorkerEntry(entry)}>
                    <header>
                      <div className="investor-history-item__title">
                        <span className="investor-history-item__icon"><Icon name={entry.kind === "problem" ? "alert" : audio.length ? "mic" : images.length ? "camera" : "report"} /></span>
                        <div>
                          <strong>{title}</strong>
                          <small>
                            {new Intl.DateTimeFormat("pl", { dateStyle: "short", timeStyle: "short" }).format(new Date(entry.occurred_at))}
                            {entry.author?.name || entry.author?.email || entry.guest_label ? ` · ${entry.author?.name || entry.author?.email || entry.guest_label}` : ""}
                          </small>
                        </div>
                      </div>
                      {entry.kind === "problem" && <em>{entry.problem_status === "resolved" ? "Rozwiązany" : "Otwarty"}</em>}
                    </header>
                    {(entry.body || entry.transcript) && <p>{entry.body || entry.transcript}</p>}
                    {visibleImages.length > 0 && (
                      <div className="investor-history-item__media">
                        {visibleImages.map((asset) => (
                          <img src={asset.url} alt={asset.original_name || "Zdjęcie z inwestycji"} loading="lazy" key={asset.id} />
                        ))}
                        {extraImages > 0 && <span>+{extraImages}</span>}
                      </div>
                    )}
                    {audio.map((asset) => <audio controls src={asset.url} key={asset.id} />)}
                    <small className="investor-history-item__comments">{entry.comments.length} komentarzy</small>
                  </button>
                </article>
              );
            })}
          </div>
        )}
      </section>
    );

    const contractorCard = (
      <section className="worker-detail-card investor-side-card">
        <h2>Wykonawca</h2>
        <div className="investor-contractor-card">
          <span>{contractorName.slice(0, 2).toUpperCase()}</span>
          <div>
            <strong>{contractorName}</strong>
            <small>{project.worker_profile ? investorContractorTypeLabel(project.worker_profile) : "Wykonawca inwestycji"}</small>
            {project.worker_profile?.email && <small>{project.worker_profile.email}</small>}
            {project.worker_profile?.phone && <small>{project.worker_profile.phone}</small>}
          </div>
        </div>
        <Button type="button" variant="secondary" icon="link" onClick={() => setShowManage(true)}>Link wykonawcy</Button>
      </section>
    );

    const stagesCard = (
      <section className="worker-detail-card investor-side-card">
        <h2>Etapy inwestycji</h2>
        {project.stages?.length ? (
          <div className="investor-stage-list">
            {project.stages.map((stage, index) => {
              const canSetCurrent = canChangeStage && stage.status !== "active";
              return (
                <article className={`investor-stage-row investor-stage-row--${stage.status}`} key={stage.id}>
                  <span className="investor-stage-row__marker">
                    {stage.status === "completed" ? <Icon name="check" /> : index + 1}
                  </span>
                  <div>
                    <strong>{stage.title}</strong>
                    <small>{stageStatusText(stage)}</small>
                  </div>
                  {canSetCurrent && (
                    <button
                      type="button"
                      className="investor-stage-row__action"
                      disabled={busyStageId === stage.id}
                      onClick={() => void setCurrentStage(stage.id)}
                    >
                      {busyStageId === stage.id ? "Ustawiam..." : "Ustaw jako aktualny"}
                    </button>
                  )}
                </article>
              );
            })}
          </div>
        ) : (
          <p className="form-note">Etapy nie są jeszcze skonfigurowane.</p>
        )}
      </section>
    );

    const problemsCard = (
      <section className="worker-detail-card investor-side-card">
        <h2>Problemy</h2>
        {openProblems === 0 ? (
          <div className="investor-problem-ok"><Icon name="check" /><strong>Brak otwartych problemów</strong><span>Wszystko przebiega zgodnie z planem.</span></div>
        ) : (
          <div className="investor-problem-warning"><Icon name="alert" /><strong>{openProblems} {openProblems === 1 ? "otwarty problem" : "otwarte problemy"}</strong><span>Sprawdź wpisy oznaczone jako problemowe.</span></div>
        )}
      </section>
    );

    const detailsCard = (
      <section className="worker-detail-card investor-side-card investor-terms-summary">
        <h2>Terminy i kwota</h2>
        <dl>
          <div><dt>Start</dt><dd>{formatContractDate(project.planned_start_date) || "Nie ustawiono"}</dd></div>
          <div><dt>Planowane zakończenie</dt><dd>{formatContractDate(project.planned_end_date) || "Nie ustawiono"}</dd></div>
          <div><dt>Zakończono</dt><dd>{project.status === "completed" ? formatProjectActivityDate(project.updated_at) : "Nie zakończono"}</dd></div>
          <div><dt>Kwota</dt><dd>{contractAmountLabel(project) || "Nie podano"}</dd></div>
        </dl>
      </section>
    );

    return (
      <div className={`worker-workspace worker-workspace--investor-detail ${advancedMode ? "worker-workspace--investor-advanced" : "worker-workspace--investor-simple"}`}>
        <header className="worker-detail-hero investor-detail-hero">
          <div className="worker-detail-topbar">
            <button type="button" className="worker-back-button" onClick={onBack}><Icon name="back" /> Wróć do inwestycji</button>
            <span><Icon name="clipboard" size={17} /> Inwestor</span>
          </div>
          <div className="worker-detail-hero__main investor-detail-hero__main">
            <span className="worker-detail-hero__icon"><Icon name="clipboard" /></span>
            <div>
              <h1>{project.name}</h1>
              <p>{project.address || "Lokalizacja nieuzupełniona"}</p>
              <p>Wykonawca: {contractorName}</p>
            </div>
            <span className={`status status--${project.status}`}>{statusLabels[project.status] || project.status}</span>
          </div>
          <div className="worker-detail-mode investor-detail-mode">
            <WorkerModeSwitch uiMode={mode} onUiModeChange={(next) => onUiModeChange?.(next)} />
          </div>
        </header>

        <main className="worker-detail-main investor-detail-main">
          {investorActions}

          {advancedMode ? (
            <div className="investor-detail-grid">
              <aside className="investor-detail-sidebar">
                {summaryCard}
                {detailsCard}
                {contractorCard}
                {stagesCard}
                {problemsCard}
              </aside>
              <div className="investor-detail-content">
                {investorHistory}
              </div>
            </div>
          ) : (
            <>
              {summaryCard}
              {investorHistory}
            </>
          )}

          {canGeneratePdfReports && (
            <div className="worker-generated-reports investor-detail-reports" ref={workerReportsRef}>
              <GeneratedReportsPanel
                projectId={projectIdForStatusActions}
                reports={reports}
                onRefresh={() => loadReports(project)}
                notify={notify}
                generatedReportLimit={3}
                loading={reportsLoading}
                error={reportError}
                copy={{
                  title: "Raporty inwestycji",
                  description: "Wszystkie raporty i podsumowania w jednym miejscu.",
                  finalDescription: "Pełne podsumowanie inwestycji.",
                }}
              />
            </div>
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
        {selectedWorkerEntry && (
          <WorkerEntryDetailsModal
            entry={selectedWorkerEntry}
            onClose={() => setSelectedWorkerEntry(null)}
            onRefresh={refreshAfterProjectMutation}
          />
        )}
        {entryModal && <NewEntryModal project={project} kind={entryModal.kind} mode={entryModal.mode} guestToken={guestToken} offlineScopeKey={offlineScopeKey} onClose={() => setEntryModal(null)} onSaved={() => { setEntryModal(null); void refreshAfterProjectMutation(); notify({ kind: "success", message: "Wpis zapisany" }); }} onQueued={() => { setEntryModal(null); onQueue(); notify({ kind: "info", message: "Wpis zapisany offline" }); }} />}
        {showManage && <ManageProjectModal project={project} user={user!} onClose={() => setShowManage(false)} onRefresh={refreshAfterProjectMutation} notify={notify} />}
      </div>
    );
  }

  if (isRoleFieldUser) {
    return (
      <div className="worker-workspace">
        <header className="worker-detail-hero">
          <div className="worker-detail-topbar">
            <button type="button" className="worker-back-button" onClick={onBack}><Icon name="back" /> Wróć do zleceń</button>
            <span><Icon name="clipboard" size={17} /> {fieldRoleTitle}</span>
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
              <p>{currentStage ? stageStatusText(currentStage) : isIndependentFieldUser ? "Ustaw aktualny etap swojej pracy." : "Szef firmy nie ustawił jeszcze etapu."}</p>
            </div>
            <button
              type="button"
              className="worker-stage-change"
              disabled={!canChangeStage}
              title={canChangeStage ? "Zmień etap pracy" : "Zmiana etapu jest niedostępna w obecnym stanie zlecenia."}
              onClick={() => {
                if (!canChangeStage) return;
                setShowWorkerStagePicker((current) => !current);
              }}
            >
                Zmień etap
            </button>
            {showWorkerStagePicker && canChangeStage && (
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

          {(canStartWorkerProject || canAddWorkerProgress || canFinishWorkerProject || project.status === "completed" || uiMode === "advanced") && (
            <section className="worker-action-panel">
              {canStartWorkerProject && <Button type="button" icon="plus" onClick={startProject}>Rozpocznij robot&#281;</Button>}
              {canAddWorkerProgress && <Button type="button" icon="plus" onClick={() => setShowAddProgressChoice(true)}>Dodaj post&#281;p</Button>}
              {canFinishWorkerProject ? (
                <Button type="button" variant="secondary" className="worker-finish-button" onClick={closeProject}>Zako&#324;cz robot&#281;</Button>
              ) : project.status === "completed" ? (
                <>
                  <div className="worker-completed-note"><Icon name="check" /> Zlecenie zako&#324;czone</div>
                  {canReopenWorkerProject && (
                    <Button type="button" variant="secondary" onClick={reopenProject}>
                      Otw&oacute;rz ponownie
                    </Button>
                  )}
                </>
              ) : null}
              {uiMode === "advanced" && canGeneratePdfReports && (
                <Button type="button" variant="secondary" icon="report" onClick={showAndScrollWorkerReports}>Raport PDF</Button>
              )}
              {uiMode === "advanced" && isIndependentFieldUser && project.can_edit_details && (
                <Button type="button" variant="secondary" icon="settings" onClick={() => setShowManage(true)}>Edytuj zlecenie</Button>
              )}
              {uiMode === "advanced" && (
                <Button
                  type="button"
                  variant="secondary"
                  icon="link"
                  disabled={!clientLink?.url}
                  onClick={() => void copyClientLink()}
                >
                  Link dla klienta
                </Button>
              )}
            </section>
          )}

          <section className="worker-detail-card">
            <div className="worker-section-heading">
              <div>
                <h2>{uiMode === "advanced" ? "Historia postępu" : "Ostatnie dodane"}</h2>
                <p>{uiMode === "advanced" ? "Wpisy, problemy, zdjęcia, audio i komentarze z tej realizacji." : "Najświeższe wpisy z tej realizacji."}</p>
              </div>
            </div>
            {workerHistoryEntries.length === 0 ? (
              <div className="worker-empty-history">
                <Icon name="camera" />
                <strong>Tu powstanie historia pracy</strong>
                <p>Dodaj pierwszy postęp: zdjęcia, opis, audio albo problem.</p>
              </div>
            ) : (
              <div className="worker-entry-list">
                {workerHistoryEntries.map((entry) => {
                  const imageAssets = entry.media.filter((asset) => asset.kind === "image");
                  const visibleImages = imageAssets.slice(0, 3);
                  const extraImageCount = Math.max(0, imageAssets.length - visibleImages.length);
                  const audioCount = entry.media.filter((asset) => asset.kind === "audio").length;
                  const hasMediaChips = imageAssets.length > 0 || audioCount > 0 || entry.comments.length > 0 || entry.kind === "problem";
                  return (
                    <article className="worker-entry-list__item" key={entry.id}>
                      <button type="button" className="worker-entry-list__main" onClick={() => setSelectedWorkerEntry(entry)}>
                        <span><Icon name={entry.kind === "problem" ? "alert" : audioCount ? "mic" : "camera"} /></span>
                        <div>
                          <strong>{entry.kind === "problem" ? "Problem" : entry.media.length ? "Dokumentacja" : "Opis"}</strong>
                          <small>
                            {new Intl.DateTimeFormat("pl", { dateStyle: "short", timeStyle: "short" }).format(new Date(entry.occurred_at))}
                            {entry.author?.name || entry.author?.email || entry.guest_label ? ` - ${entry.author?.name || entry.author?.email || entry.guest_label}` : ""}
                          </small>
                          {uiMode === "advanced" && (entry.body || entry.transcript) && <p className="worker-entry-list__excerpt">{entry.body || entry.transcript}</p>}
                          {uiMode === "advanced" && visibleImages.length > 0 && (
                            <div className="worker-entry-media-preview" aria-label={`Zdjecia: ${imageAssets.length}`}>
                              {visibleImages.map((asset) => (
                                <img src={asset.url} alt={asset.original_name || "Zdjecie z wpisu"} loading="lazy" key={asset.id} />
                              ))}
                              {extraImageCount > 0 && <span>+{extraImageCount}</span>}
                            </div>
                          )}
                          {uiMode === "advanced" && hasMediaChips && (
                            <div className="worker-entry-media-chips">
                              {imageAssets.length > 0 && <span>{imageAssets.length} zdj&#281;&#263;</span>}
                              {audioCount > 0 && <span>Audio</span>}
                              {entry.comments.length > 0 && <span>{entry.comments.length} komentarzy</span>}
                              {entry.kind === "problem" && <span>{entry.problem_status === "resolved" ? "Problem rozwi&#261;zany" : "Problem otwarty"}</span>}
                            </div>
                          )}
                        </div>
                        <Icon name="back" className="worker-entry-list__arrow" />
                      </button>
                      {canDeleteEntry(entry) && (
                        <button type="button" className="worker-entry-list__delete" onClick={() => setDeleteEntryTarget(entry)}>
                          Usu&#324; dokumentacj&#281;
                        </button>
                      )}
                    </article>
                  );
                })}
              </div>
            )}
          </section>



          {uiMode === "advanced" && (
            <>
              <section className="worker-detail-card worker-advanced-overview">
                <div><small>Start</small><strong>{formatContractDate(project.planned_start_date) || "Nie ustawiono"}</strong></div>
                <div><small>Koniec</small><strong>{formatContractDate(project.planned_end_date) || "Nie ustawiono"}</strong></div>
                <div><small>Ostatnia aktywność</small><strong>{formatProjectActivityDate(project.updated_at || project.created_at) || "Brak daty"}</strong></div>
                <div><small>Materiały</small><strong>{entries.length} wpisów · {workerProblemCount} problemów</strong><span>{workerImageCount} zdjęć · {workerAudioCount} audio</span></div>
              </section>
              {showClientLink && (
                <section className="worker-detail-card worker-client-link-card">
                  <div className="worker-section-heading">
                    <div><h2>Link dla klienta</h2><p>{clientLink?.url ? "Gotowy podgląd publiczny zlecenia." : isIndependentFieldUser ? "Brak aktywnego linku klienta dla tego zlecenia." : "Brak linku klienta — poproś szefa o wygenerowanie linku."}</p></div>
                  </div>
                  {clientLink?.url ? (
                    <div className="share-result client-link-result">
                      <input value={clientLink.url} readOnly />
                      <Button variant="secondary" onClick={() => void copyToClipboard(clientLink.url)}>Kopiuj link</Button>
                      <a className="button button--secondary" href={clientLink.url} target="_blank" rel="noreferrer">Otwórz podgląd</a>
                    </div>
                  ) : (
                    <p className="form-note">{isIndependentFieldUser ? "Link klienta utworzysz w edycji zlecenia, jeśli jest potrzebny." : "Link klienta tworzy i udostępnia szef firmy."}</p>
                  )}
                </section>
              )}
              {clientCoverCard}
              {showReports && canGeneratePdfReports && (
                <div className="worker-generated-reports" ref={workerReportsRef}>
                  <GeneratedReportsPanel
                    projectId={project.id}
                    reports={reports}
                    onRefresh={() => loadReports(project)}
                    notify={notify}
                    generatedReportLimit={3}
                    loading={reportsLoading}
                    error={reportError}
                  />
                </div>
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
        {selectedWorkerEntry && (
          <WorkerEntryDetailsModal
            entry={selectedWorkerEntry}
            onClose={() => setSelectedWorkerEntry(null)}
            onRefresh={refreshAfterProjectMutation}
          />
        )}
        {deleteEntryTarget && (
          <Modal title="Usunąć dokumentację?" onClose={() => setDeleteEntryTarget(null)}>
            <div className="delete-entry-confirm">
              <p>Ten wpis zostanie usuni&#281;ty z historii post&#281;pu. Projekt i wygenerowane raporty PDF pozostan&#261; bez zmian.</p>
              <div className="modal-actions">
                <Button type="button" variant="secondary" onClick={() => setDeleteEntryTarget(null)}>Anuluj</Button>
                <Button type="button" variant="danger" onClick={() => void deleteDocumentationEntry()}>Usu&#324; dokumentacj&#281;</Button>
              </div>
            </div>
          </Modal>
        )}
        {entryModal && <NewEntryModal project={project} kind={entryModal.kind} mode={entryModal.mode} guestToken={guestToken} offlineScopeKey={offlineScopeKey} onClose={() => setEntryModal(null)} onSaved={() => { setEntryModal(null); void refreshAfterProjectMutation(); notify({ kind: "success", message: "Wpis zapisany" }); }} onQueued={() => { setEntryModal(null); onQueue(); notify({ kind: "info", message: "Wpis zapisany offline i czeka na wysłanie" }); }} />}
        {isIndependentFieldUser && showManage && <ManageProjectModal project={project} user={user!} onClose={() => setShowManage(false)} onRefresh={refreshAfterProjectMutation} notify={notify} />}
        {showClientCoverPicker && (
          <ClientCoverPicker
            images={projectImages}
            selectedId={project.client_cover_media_id}
            busy={coverBusy}
            onSelect={(mediaId) => void updateClientCover(mediaId)}
            onClear={() => void updateClientCover(null)}
            onClose={() => setShowClientCoverPicker(false)}
          />
        )}
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
            <FieldAction icon="alert" title="Problem" subtitle="Usterka lub decyzja" tone="red" onClick={() => setEntryModal({ kind: "problem", mode: "text" })} />
          </div>}
          {canGeneratePdfReports && (
            <GeneratedReportsPanel
              projectId={projectIdForStatusActions}
              reports={reports}
              guestToken={guestToken}
              onRefresh={() => loadReports(project)}
              notify={notify}
              loading={reportsLoading}
              error={reportError}
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
            {entries.length === 0 ? <EmptyState icon="camera" title="Jeszcze bez wpisów" text="Dodaj pierwszy postęp prac: zdjęcia i krótki opis." /> : entries.map((entry) => <TimelineEntry item={entry} guestToken={guestToken} onRefresh={refreshAfterProjectMutation} canDelete={canDeleteEntry(entry)} onDelete={setDeleteEntryTarget} key={entry.id} />)}
          </section>
        </main>
        {entryModal && <NewEntryModal project={project} kind={entryModal.kind} mode={entryModal.mode} guestToken={guestToken} offlineScopeKey={offlineScopeKey} onClose={() => setEntryModal(null)} onSaved={() => { setEntryModal(null); void refreshAfterProjectMutation(); notify({ kind: "success", message: "Wpis zapisany" }); }} onQueued={() => { setEntryModal(null); onQueue(); notify({ kind: "info", message: "Wpis zapisany offline i czeka na wysłanie" }); }} />}
        {showReports && <ReportModal project={project} reports={reports} onClose={() => setShowReports(false)} onRefresh={refreshAfterProjectMutation} notify={notify} />}
      </div>
    );
  }

  return (
    <div className={`page project-page ${isInvestorPanelUser ? "project-page--investor" : ""}`}>
      <header className="project-header">
        <button className="back-button" onClick={onBack}><Icon name="back" /> {isInvestorPanelUser ? "Wróć do inwestycji" : "Wróć do zleceń"}</button>
        <div className="project-header__main">
          <div><span className={`status status--${project.status}`}>{statusLabels[project.status]}</span><h1>{project.name}</h1><p>{project.client_name} · {project.address}</p></div>
          <div className="project-header__actions">
            {isInvestorPanelUser && canAdd && <Button variant="secondary" icon="plus" onClick={() => setShowAddProgressChoice(true)}>Dodaj wpis</Button>}
            {isInvestorPanelUser && canGeneratePdfReports && <Button variant="secondary" icon="report" onClick={() => setShowReports(true)}>Raport PDF</Button>}
            {!guestToken && user?.profile_type !== "investor" && !isCompanyWorker(user) && clientLink && <Button variant="secondary" icon="link" onClick={copyClientLink}>Link klienta</Button>}
            {canCloseProject && <Button variant="danger" onClick={closeProject}>Zamknij zlecenie</Button>}
            {canReopenProject && project.status === "completed" && <Button variant="success" onClick={reopenProject}>Otwórz ponownie</Button>}
            {isInvestorPanelUser && (
              <Button
                variant="secondary"
                icon="link"
                onClick={() => {
                  if (project.can_edit_details) {
                    setShowManage(true);
                    notify({ kind: "info", message: "Link wykonawcy znajdziesz w sekcji wykonawcy edycji inwestycji." });
                  } else {
                    notify({ kind: "info", message: "Link wykonawcy jest dostępny tylko dla osoby zarządzającej inwestycją." });
                  }
                }}
              >
                Link wykonawcy
              </Button>
            )}
            {!guestToken && project.can_edit_details && <Button variant="secondary" icon="settings" onClick={() => setShowManage(true)}>{isInvestorPanelUser ? "Edytuj inwestycję" : "Edytuj zlecenie"}</Button>}
          </div>
        </div>
        {!isInvestorPanelUser && showClientLink && clientLink?.url && (
          <div className="share-result client-link-result">
            <input value={clientLink.url} readOnly />
            <Button variant="secondary" onClick={() => void copyToClipboard(clientLink.url)}>Kopiuj link</Button>
          </div>
        )}
      </header>
      <div className="project-layout">
        <aside className="project-summary panel">
          <h3>{isInvestorPanelUser ? "Podsumowanie inwestycji" : "Podsumowanie zlecenia"}</h3>
          <div className="progress-value"><strong>{progress}%</strong><span>{completedCount} z {project.stages?.length || 0} etapów ukończonych</span></div>
          <div className="progress"><i style={{ width: `${progress}%` }} /></div>
          <ContractTermsPanel project={project} />
          {stages}
          {!guestToken && <div className="summary-meta"><div><small>{isInvestorPanelUser ? "Wykonawca" : "Klient"}</small><strong>{isInvestorPanelUser ? projectPartyValue(user!, project) : project.client_name || "—"}</strong></div><div><small>Adres</small><strong>{project.address || "—"}</strong></div><div><small>Rola</small><strong>{project.role || "gość"}</strong></div></div>}
        </aside>
        <main className="project-timeline panel">
          <div className="panel__header">
            <div><h2>{isInvestorPanelUser ? "Historia postępu inwestycji" : "Postęp i zarządzanie zleceniem"}</h2><p>Zdjęcia, opisy, ustalenia i problemy w jednej osi czasu.</p></div>
            {canAdd && <div className="quick-buttons"><button onClick={() => isInvestorPanelUser ? setShowAddProgressChoice(true) : setEntryModal({ kind: "update", mode: "photo" })}><Icon name="camera" /> {isInvestorPanelUser ? "Dodaj wpis" : "Dodaj postęp"}</button><button className="problem" onClick={() => setEntryModal({ kind: "problem", mode: "text" })}><Icon name="alert" /> Zgłoś problem</button></div>}
          </div>
          {entries.length === 0 ? <EmptyState icon="camera" title="Tu powstanie historia pracy" text="Dodaj pierwszy postęp: zdjęcia oraz opis głosowy lub tekstowy." /> : <div className="timeline">{entries.map((entry) => <TimelineEntry item={entry} guestToken={guestToken} onRefresh={refreshAfterProjectMutation} canDelete={canDeleteEntry(entry)} onDelete={setDeleteEntryTarget} key={entry.id} />)}</div>}
        </main>
      </div>
      {canGeneratePdfReports && (
        <GeneratedReportsPanel
          projectId={projectIdForStatusActions}
          reports={reports}
          guestToken={guestToken}
          onRefresh={() => loadReports(project)}
          notify={notify}
          loading={reportsLoading}
          error={reportError}
        />
      )}
      {clientCoverCard}
      {isInvestorPanelUser && showAddProgressChoice && (
        <AddProgressChoice
          onClose={() => setShowAddProgressChoice(false)}
          onPick={(entry) => {
            setShowAddProgressChoice(false);
            setEntryModal(entry);
          }}
        />
      )}
      {entryModal && <NewEntryModal project={project} kind={entryModal.kind} mode={entryModal.mode} guestToken={guestToken} offlineScopeKey={offlineScopeKey} onClose={() => setEntryModal(null)} onSaved={() => { setEntryModal(null); void refreshAfterProjectMutation(); notify({ kind: "success", message: "Wpis zapisany" }); }} onQueued={() => { setEntryModal(null); onQueue(); notify({ kind: "info", message: "Wpis zapisany offline" }); }} />}
      {showReports && <ReportModal project={project} reports={reports} onClose={() => setShowReports(false)} onRefresh={refreshAfterProjectMutation} notify={notify} />}
      {showManage && <ManageProjectModal project={project} user={user} onClose={() => setShowManage(false)} onRefresh={refreshAfterProjectMutation} notify={notify} />}
      {showClientCoverPicker && (
        <ClientCoverPicker
          images={projectImages}
          selectedId={project.client_cover_media_id}
          busy={coverBusy}
          onSelect={(mediaId) => void updateClientCover(mediaId)}
          onClear={() => void updateClientCover(null)}
          onClose={() => setShowClientCoverPicker(false)}
        />
      )}
    </div>
  );
}

function ClientCoverPicker({
  images,
  selectedId,
  busy,
  onSelect,
  onClear,
  onClose,
}: {
  images: Array<{ asset: MediaAsset; entry: Entry }>;
  selectedId?: string | null;
  busy: boolean;
  onSelect: (mediaId: string) => void;
  onClear: () => void;
  onClose: () => void;
}) {
  const formatter = new Intl.DateTimeFormat("pl", { dateStyle: "short", timeStyle: "short" });
  return (
    <Modal title="Wybierz zdjęcie główne" onClose={onClose}>
      <div className="client-cover-picker">
        <p className="form-note">To zdjęcie będzie pierwszym obrazem w linku klienta. Jeśli nie wybierzesz żadnego, klient zobaczy najnowsze zdjęcie z historii.</p>
        {images.length === 0 ? (
          <EmptyState icon="camera" title="Brak zdjęć" text="Dodaj zdjęcie do historii postępu, żeby ustawić obraz główny linku klienta." />
        ) : (
          <div className="client-cover-grid">
            {images.map(({ asset, entry }) => (
              <button
                type="button"
                className={asset.id === selectedId ? "selected" : ""}
                disabled={busy}
                onClick={() => onSelect(asset.id)}
                key={asset.id}
              >
                <img src={asset.url} alt={asset.original_name || "Zdjęcie postępu"} loading="lazy" />
                <span>{formatter.format(new Date(entry.occurred_at))}</span>
                {asset.id === selectedId && <strong>Wybrane</strong>}
              </button>
            ))}
          </div>
        )}
        <div className="modal-actions">
          <Button type="button" variant="secondary" disabled={busy || !selectedId} onClick={onClear}>Wyczyść wybór</Button>
          <Button type="button" variant="secondary" onClick={onClose}>Zamknij</Button>
        </div>
      </div>
    </Modal>
  );
}

function ReportsPage({ user, projects, onOpen }: { user: User; projects: Project[]; onOpen: (project: Project) => void }) {
  const [tab, setTab] = useState<"all" | "open" | "history">("all");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "assigned" | "in_progress" | "completed">("all");
  const [sortBy, setSortBy] = useState<"issued" | "ended" | "name">("issued");
  const [sortDirection, setSortDirection] = useState<"newest" | "oldest">("newest");
  const [collapsedReports, setCollapsedReports] = useState<string[]>([]);
  const [reportModalProject, setReportModalProject] = useState<Project | null>(null);
  const [reportModalReports, setReportModalReports] = useState<Report[]>([]);
  const [reportModalLoading, setReportModalLoading] = useState(false);
  const [reportModalError, setReportModalError] = useState("");
  const [busyReportAction, setBusyReportAction] = useState<string | null>(null);
  const investorReports = isInvestor(user);
  const companyOwnerReports = isCompanyOwner(user);

  useEffect(() => {
    if (!reportModalProject) return;
    let active = true;
    setReportModalLoading(true);
    setReportModalError("");
    api<Report[]>(`/projects/${reportModalProject.id}/reports`)
      .then((items) => {
        if (active) setReportModalReports(items);
      })
      .catch((reason) => {
        if (active) {
          setReportModalReports([]);
          setReportModalError(reason instanceof Error ? reason.message : "Nie udało się wczytać raportów PDF");
        }
      })
      .finally(() => {
        if (active) setReportModalLoading(false);
      });
    return () => { active = false; };
  }, [reportModalProject]);

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

  function openReportModal(project: Project) {
    setReportModalReports([]);
    setReportModalError("");
    setReportModalProject(project);
  }

  async function handleReportPdf(report: Report, action: "open" | "download") {
    const pdfHref = reportPdfHref(report);
    if (!pdfHref) {
      setReportModalError("Raport PDF nie jest jeszcze gotowy");
      return;
    }
    setBusyReportAction(`${report.id}:${action}`);
    setReportModalError("");
    try {
      const blob = await fetchPdfBlob(pdfHref);
      openBlobUrl(blob, `${reportTypeLabel(report)}-${reportDisplayDate(report)}.pdf`, action === "download");
    } catch (reason) {
      setReportModalError(reason instanceof Error ? reason.message : "Nie udało się otworzyć raportu PDF");
    } finally {
      setBusyReportAction(null);
    }
  }

  const openProjects = projects.filter((project) => project.status !== "completed");
  const historicalProjects = projects.filter((project) => project.status === "completed");
  const allReportMaterial = projects.reduce((sum, project) => sum + reportMaterialCount(project), 0);
  const openReportMaterial = openProjects.reduce((sum, project) => sum + reportMaterialCount(project), 0);
  const historicalReportMaterial = historicalProjects.reduce((sum, project) => sum + reportMaterialCount(project), 0);
  const multiReportProjects = projects.filter((project) => reportMaterialCount(project) > 1).length;
  const investorFilterCards = [
    {
      key: "all" as const,
      title: "Wszystkie",
      count: allReportMaterial,
      text: "Wpisy i raporty z inwestycji",
      icon: "report" as const,
      tone: "blue",
    },
    {
      key: "open" as const,
      title: "Otwarte",
      count: openReportMaterial,
      text: `${openProjects.length} inwestycji w toku`,
      icon: "sync" as const,
      tone: "green",
    },
    {
      key: "history" as const,
      title: "Historyczne",
      count: historicalReportMaterial,
      text: `${historicalProjects.length} zakończonych`,
      icon: "clipboard" as const,
      tone: "orange",
    },
  ];
  const queryText = query.trim().toLowerCase();
  const source = tab === "all" ? projects : tab === "open" ? openProjects : historicalProjects;
  const visibleProjects = [...source]
    .filter((project) =>
      `${project.name} ${project.client_name || ""} ${project.address || ""} ${project.worker_profile?.label || ""}`
        .toLowerCase()
        .includes(queryText),
    )
    .sort((left, right) => {
      if (sortBy === "name") {
        const result = left.name.localeCompare(right.name, "pl", { sensitivity: "base" });
        return sortDirection === "newest" ? result : -result;
      }
      const result = reportSortDate(left) - reportSortDate(right);
      return sortDirection === "newest" ? -result : result;
    });

  if (isIndependentContractor(user) || investorReports || companyOwnerReports) {
    const reportProjects = investorReports || companyOwnerReports ? projects : projects.filter((project) => reportMaterialCount(project) > 0);
    const independentOpenProjects = reportProjects.filter((project) => project.status !== "completed");
    const independentHistoricalProjects = reportProjects.filter((project) => project.status === "completed");
    const independentSource = tab === "all"
      ? reportProjects
      : tab === "open"
        ? independentOpenProjects
        : independentHistoricalProjects;
    const filteredProjects = independentSource
      .filter((project) => statusFilter === "all" || project.status === statusFilter)
      .filter((project) =>
        `${project.name} ${project.client_name || ""} ${project.address || ""} raport zlecenia raport`
          .toLowerCase()
          .includes(queryText),
      )
      .sort((left, right) => {
        if (sortBy === "name") {
          const result = left.name.localeCompare(right.name, "pl", { sensitivity: "base" });
          return sortDirection === "newest" ? result : -result;
        }
        const result = reportSortDate(left) - reportSortDate(right);
        return sortDirection === "newest" ? -result : result;
      });
    const allMaterial = reportProjects.reduce((sum, project) => sum + reportMaterialCount(project), 0);
    const openMaterial = independentOpenProjects.reduce((sum, project) => sum + reportMaterialCount(project), 0);
    const historicalMaterial = independentHistoricalProjects.reduce((sum, project) => sum + reportMaterialCount(project), 0);
    const activeFilterCards = [
      {
        key: "all" as const,
        title: "Wszystkie",
        count: allMaterial,
        text: investorReports ? "Wpisy i raporty z inwestycji" : companyOwnerReports ? "Wpisy i raporty zleceń firmy" : "Wszystkie wpisy i raporty",
        icon: "report" as const,
        tone: "blue",
      },
      {
        key: "open" as const,
        title: "Otwarte",
        count: openMaterial,
        text: investorReports ? `${independentOpenProjects.length} inwestycji w toku` : companyOwnerReports ? `${independentOpenProjects.length} zleceń otwartych` : "Wymagają zakończenia",
        icon: "sync" as const,
        tone: "green",
      },
      {
        key: "history" as const,
        title: "Historyczne",
        count: historicalMaterial,
        text: investorReports ? `${independentHistoricalProjects.length} zakończonych` : companyOwnerReports ? `${independentHistoricalProjects.length} zleceń zakończonych` : "Zakończone",
        icon: "clipboard" as const,
        tone: "orange",
      },
    ];

    return (
      <div className={`page reports-page independent-reports-page ${investorReports ? "independent-reports-page--investor" : ""} ${companyOwnerReports ? "independent-reports-page--company-owner" : ""}`}>
        <header className="page-header">
          <div>
            <span className="eyebrow">{investorReports ? "Moje raporty" : "Dokumentacja"}</span>
            <h1>Raporty</h1>
            <p>{investorReports ? "Przeglądaj raporty i wpisy z Twoich inwestycji." : companyOwnerReports ? "Przeglądaj raporty, wpisy i materiały ze zleceń firmy." : "Przeglądaj projekty raportowe i otwieraj raporty tworzone w ramach zleceń."}</p>
          </div>
        </header>

        <section className="report-filter-cards" aria-label="Filtry raportów">
          {activeFilterCards.map((card) => (
            <button
              key={card.key}
              type="button"
              className={`report-filter-card report-filter-card--${card.tone} ${tab === card.key ? "active" : ""}`}
              onClick={() => setTab(card.key)}
            >
              <span><Icon name={card.icon} /></span>
              <div>
                <strong>{card.title}</strong>
                <b>{card.count}</b>
                <small>{card.text}</small>
              </div>
              {tab === card.key && <em><Icon name="check" size={16} /></em>}
            </button>
          ))}
        </section>

        <section className="report-toolbar independent-report-toolbar" aria-label="Wyszukiwanie i sortowanie raportów">
          <input
            type="search"
            placeholder={investorReports ? "Szukaj po nazwie inwestycji, wykonawcy lub adresie..." : "Szukaj po nazwie zlecenia, kliencie lub wykonawcy..."}
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

        <section className="report-project-list independent-report-list">
          {reportProjects.length === 0 ? (
            <EmptyState icon="report" title="Brak raportów" text={investorReports ? "Raporty pojawią się tutaj po wpisach wykonawców w Twoich inwestycjach." : companyOwnerReports ? "Raporty pojawią się tutaj po postępach ekip i wygenerowaniu PDF w zleceniu." : "Raporty pojawią się tutaj po dodaniu postępu i wygenerowaniu raportu w zleceniu."} />
          ) : filteredProjects.length === 0 ? (
            <EmptyState icon="report" title="Brak wyników" text="Zmień filtr lub wyszukiwaną frazę." />
          ) : filteredProjects.map((project) => {
            const count = reportMaterialCount(project);
            const isExpanded = collapsedReports.includes(project.id);
            const issuedDate = new Intl.DateTimeFormat("pl").format(new Date(project.updated_at || project.created_at));
            const safeStatusLabel = statusLabels[project.status] || project.status;
            return (
              <article className={`report-project-card independent-report-card ${isExpanded ? "is-expanded" : ""}`} key={project.id}>
                <header>
                  <span className="report-project-card__icon"><Icon name="report" /></span>
                  <div className="report-project-card__main">
                    <h2>{project.name}</h2>
                    <p>
                      {project.address || "Adres nieuzupełniony"}
                      {!investorReports && (project.client_name ? ` · Klient: ${project.client_name}` : " · Klient: Brak klienta")}
                    </p>
                  </div>
                  <span className={`status status--${project.status}`}>{safeStatusLabel}</span>
                  <dl>
                    <div><dt>{projectPartyLabel(user)}</dt><dd>{projectPartyValue(user, project)}</dd></div>
                    <div><dt>Planowane zakończenie</dt><dd>{formatContractDate(project.planned_end_date) || "Nie ustawiono"}</dd></div>
                  </dl>
                  <button
                    type="button"
                    className="report-count-badge"
                    onClick={(event) => {
                      event.stopPropagation();
                      toggleReportDetails(project.id);
                    }}
                    aria-expanded={isExpanded}
                  >
                    {reportBadge(project)}
                    <Icon name="back" size={15} />
                  </button>
                  <div className="independent-report-card__actions">
                    <Button type="button" variant="secondary" onClick={() => onOpen(project)}>{investorReports ? "Otwórz" : "Otwórz zlecenie"}</Button>
                    <Button type="button" variant="secondary" icon="report" onClick={() => openReportModal(project)}>{investorReports ? "Raport PDF" : "Otwórz raporty PDF"}</Button>
                  </div>
                </header>
                {isExpanded && (
                  <section className="report-materials">
                    <div className="report-materials__heading">
                      <span>Raporty do tego zlecenia</span>
                      <small>{reportBadge(project)}</small>
                    </div>
                    <div className="report-sublist">
                      <article>
                        <span><Icon name="report" /></span>
                        <div>
                          <span className="report-row-label">Raport</span>
                          <strong>{investorReports ? "Raport inwestycji" : "Raport zlecenia"}</strong>
                          <small>{`Materiał raportowy z ${count} ${count === 1 ? "wpisu" : "wpisów"}.`}</small>
                        </div>
                        <div><small>Data wystawienia</small><strong>{issuedDate}</strong></div>
                        <div><small>Etap / Status</small><span className={`status status--${project.status}`}>{safeStatusLabel}</span></div>
                        <Button type="button" variant="secondary" icon="report" onClick={() => openReportModal(project)}>{investorReports ? "Otwórz raport" : "Otwórz raporty PDF"}</Button>
                      </article>
                    </div>
                  </section>
                )}
              </article>
            );
          })}
        </section>

        {filteredProjects.length > 0 && (
          <footer className="report-pagination-summary">
              <span>1-{filteredProjects.length} z {filteredProjects.length} {investorReports ? "inwestycji" : "zleceń"}</span>
            <div aria-label="Paginacja raportów">
              <button type="button" disabled><Icon name="back" size={15} /></button>
              <b>1</b>
              <button type="button" disabled><Icon name="back" size={15} /></button>
            </div>
          </footer>
        )}
        {reportModalProject && (
          <Modal title="Raporty PDF" onClose={() => setReportModalProject(null)} wide>
            <div className="report-pdf-modal">
              <section className="report-pdf-modal__preview">
                <span><Icon name="report" /></span>
                <div>
                  <h3>{reportModalProject.name}</h3>
                  <p>
                    {[reportModalProject.client_name ? `Klient: ${reportModalProject.client_name}` : "", reportModalProject.address || ""]
                      .filter(Boolean)
                      .join(" · ") || "Materiały raportowe z tego zlecenia"}
                  </p>
                  <small>Podgląd materiałów raportowych</small>
                </div>
              </section>
              {reportModalError && <p className="form-error">{reportModalError}</p>}
              {reportModalLoading ? (
                <div className="loading-screen"><span className="spinner" /> Ładowanie raportów...</div>
              ) : reportModalReports.filter((report) => ["daily", "final"].includes(report.report_type) && report.pdf_url).length === 0 ? (
                <EmptyState icon="report" title="Brak raportów PDF" text="Raporty pojawią się tutaj po wygenerowaniu raportu w szczególe zlecenia.">
                  <Button type="button" variant="secondary" onClick={() => { const project = reportModalProject; setReportModalProject(null); onOpen(project); }}>Otwórz zlecenie</Button>
                </EmptyState>
              ) : (
                <div className="report-pdf-modal__list">
                  {reportModalReports
                    .filter((report) => ["daily", "final"].includes(report.report_type) && report.pdf_url)
                    .map((report) => (
                      <article key={report.id}>
                        <span><Icon name="report" /></span>
                        <div>
                          <strong>{reportTypeLabel(report)}</strong>
                          <small>{reportDisplayDate(report)}</small>
                        </div>
                        <div>
                          <small>Status</small>
                          <span className={`report-status report-status--${report.status}`}>{reportStatusLabel(report)}</span>
                        </div>
                        <div>
                          <small>Materiały</small>
                          <b>{reportMaterialCount(reportModalProject)} wpisów</b>
                        </div>
                        <div className="generated-report-actions">
                          <Button
                            type="button"
                            variant="secondary"
                            busy={busyReportAction === `${report.id}:open`}
                            onClick={() => void handleReportPdf(report, "open")}
                          >
                            Otwórz PDF
                          </Button>
                          <Button
                            type="button"
                            busy={busyReportAction === `${report.id}:download`}
                            onClick={() => void handleReportPdf(report, "download")}
                          >
                            Pobierz
                          </Button>
                        </div>
                      </article>
                    ))}
                </div>
              )}
            </div>
          </Modal>
        )}
      </div>
    );
  }

  return (
    <div className={`page reports-page ${investorReports ? "reports-page--investor" : ""}`}>
      <header className="page-header">
        <div>
          <span className="eyebrow">{investorReports ? "Moje raporty" : "Dokumentacja"}</span>
          <h1>Raporty</h1>
          <p>{investorReports ? "Przeglądaj raporty i wpisy z Twoich inwestycji." : "Przeglądaj projekty raportowe i otwieraj raporty tworzone w ramach zleceń."}</p>
        </div>
      </header>

      {investorReports ? (
        <section className="report-filter-cards investor-report-filter-cards" aria-label="Filtry raportów inwestora">
          {investorFilterCards.map((card) => (
            <button
              key={card.key}
              type="button"
              className={`report-filter-card report-filter-card--${card.tone} ${tab === card.key ? "active" : ""}`}
              onClick={() => setTab(card.key)}
            >
              <span><Icon name={card.icon} /></span>
              <div>
                <strong>{card.title}</strong>
                <b>{card.count}</b>
                <small>{card.text}</small>
              </div>
              {tab === card.key && <em><Icon name="check" size={16} /></em>}
            </button>
          ))}
        </section>
      ) : (
        <>
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
        </>
      )}

      <section className={`report-toolbar ${investorReports ? "investor-report-toolbar independent-report-toolbar" : ""}`}>
        <input
          type="search"
          placeholder={investorReports ? "Szukaj po nazwie inwestycji, wykonawcy lub adresie..." : "Szukaj po nazwie zlecenia, kliencie lub wykonawcy..."}
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

      <section className={`report-project-list ${investorReports ? "investor-report-list" : ""}`}>
        {visibleProjects.length === 0 ? (
          <EmptyState icon="report" title="Brak raportów w tym widoku" text="Zmień zakładkę albo frazę wyszukiwania." />
        ) : visibleProjects.map((project) => {
          const count = reportMaterialCount(project);
          const isExpanded = count > 0 && !collapsedReports.includes(project.id);
          return (
            <article className={`report-project-card panel ${investorReports ? "investor-report-card" : ""}`} key={project.id}>
              <header>
                <span className="report-project-card__icon"><Icon name="report" /></span>
                <div className="report-project-card__main">
                  <h2>{project.name}</h2>
                  <p>{project.address || "Adres nieuzupełniony"}{project.client_name && !investorReports ? ` · Klient: ${project.client_name}` : ""}</p>
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
                        <strong>{investorReports ? "Raport inwestycji" : "Raport zlecenia"}</strong>
                        <small>{count > 0 ? `Materiał raportowy z ${count} ${count === 1 ? "wpisu" : "wpisów"}.` : "Brak wpisów postępu do raportu."}</small>
                      </div>
                      <div><small>Data wystawienia</small><strong>{new Intl.DateTimeFormat("pl").format(new Date(project.updated_at || project.created_at))}</strong></div>
                      <div><small>Etap / Status</small><span className={`status status--${project.status}`}>{statusLabels[project.status] || project.status}</span></div>
                      <div className="report-row-actions">
                        <Button type="button" variant="secondary" onClick={() => onOpen(project)}>{investorReports ? "Otwórz" : "Otwórz raport"}</Button>
                        {investorReports && <Button type="button" variant="secondary" icon="report" onClick={() => onOpen(project)}>Raport PDF</Button>}
                      </div>
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
  const [contractorPath, setContractorPath] = useState<"account" | "link">("account");
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
  const accountWorkers = workers.filter((worker) => worker.account_type === "account").length;
  const linkOnlyWorkers = workers.filter((worker) => worker.account_type === "link_only").length + workerLinks.filter((link) => !link.revoked_at).length;
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
            <p>Zarządzaj wykonawcami przypisanymi do Twoich inwestycji.</p>
          </div>
          <div className="company-team-meta">
            <span>{activeWorkers.length} aktywnych</span>
            <span>{inactiveWorkers.length} nieaktywnych</span>
            <Button type="button" icon="plus" onClick={() => setShowAddContractor(true)}>Dodaj wykonawcę</Button>
          </div>
        </div>
        <div className="investor-contractor-summary" aria-label="Typy dostępu wykonawców">
          <article>
            <span><Icon name="users" /></span>
            <div>
              <strong>Konto Pan Majster</strong>
              <small>{accountWorkers} {accountWorkers === 1 ? "wykonawca z kontem" : "wykonawców z kontem"}</small>
            </div>
          </article>
          <article>
            <span><Icon name="link" /></span>
            <div>
              <strong>Przez link</strong>
              <small>{linkOnlyWorkers} {linkOnlyWorkers === 1 ? "dostęp link-only" : "dostępów link-only"}</small>
            </div>
          </article>
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
          <span>Typ / status</span>
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
                  <strong>{investorContractorTypeLabel(worker)}</strong>
                  <span className={`status ${worker.active ? "status--active" : "status--archived"}`}>{worker.active ? "Aktywny" : "Nieaktywny"}</span>
                  <small>{worker.account_type === "account" ? "konto Pan Majster" : "link-only"} · ostatnio {investorContractorActivityLabel(worker)}</small>
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
                  <Button type="button" variant="secondary" onClick={() => notify({ kind: "info", message: "Przypisanie wykonawcy zrobisz w edycji konkretnej inwestycji." })}>Przypisz do inwestycji</Button>
                  <Button type="button" variant="secondary" icon="link" onClick={() => notify({ kind: "info", message: "Link wykonawcy jest generowany dla konkretnej inwestycji w jej edycji." })}>Link wykonawcy</Button>
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
          <summary>Wykonawcy przez link <span>{workerLinks.length}</span></summary>
          <div className="member-list worker-link-list worker-link-list--compact">
            {workerLinks.map((link) => (
              <article key={link.id}>
                <span>{link.label.slice(0, 2).toUpperCase()}</span>
                <div>
                  <strong>{link.label}</strong>
                  <small>{link.email || "Bez e-maila"} · {link.project_name || "Inwestycja"} · Link wykonawcy · link-only</small>
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
              Dodajesz prywatnego wykonawcę do swoich inwestycji. To nie jest wyszukiwarka ani publiczna baza firm.
            </p>
            <div className="contractor-path-choice" role="group" aria-label="Sposób dodania wykonawcy">
              <button type="button" className={contractorPath === "account" ? "active" : ""} onClick={() => setContractorPath("account")}>
                <Icon name="users" />
                <strong>Wykonawca z kontem Pan Majster</strong>
                <small>Zaprosisz wykonawcę, który ma lub utworzy konto i będzie mógł pracować w aplikacji.</small>
              </button>
              <button type="button" className={contractorPath === "link" ? "active" : ""} onClick={() => setContractorPath("link")}>
                <Icon name="link" />
                <strong>Wykonawca przez link</strong>
                <small>Dla wykonawcy bez konta. Link będzie przypisany do konkretnej inwestycji.</small>
              </button>
            </div>
            <label>Typ<select name="profile_kind" defaultValue="craftsman"><option value="craftsman">Wykonawca</option><option value="crew">Firma / ekipa zewnętrzna</option></select></label>
            <label>Nazwa wykonawcy<input name="label" required placeholder="np. Firma remontowa albo hydraulik" autoFocus /></label>
            <div className="form-row">
              <label>{contractorPath === "account" ? "E-mail do zaproszenia" : "E-mail opcjonalnie"}<input type="email" name="email" placeholder={contractorPath === "account" ? "np. wykonawca@example.com" : "Możesz zostawić puste"} /></label>
              <label>Telefon opcjonalnie<input name="phone" /></label>
            </div>
            <label>Profesja / specjalizacja wykonawcy<textarea name="note" rows={2} placeholder="np. hydraulik, ogrodnik, glazurnik" /></label>
            <Button type="submit" busy={busy} icon="plus">{contractorPath === "account" ? "Dodaj wykonawcę" : "Dodaj wykonawcę przez link"}</Button>
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
              <div><dt>Typ</dt><dd>{investorContractorTypeLabel(previewWorker)}{previewWorker.account_type === "link_only" ? " · link-only" : ""}</dd></div>
              <div><dt>Ostatnia aktywność</dt><dd>{investorContractorActivityLabel(previewWorker)}</dd></div>
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
              Pracownik z kontem dostaje zaproszenie przez e-mail po potwierdzeniu kodem.
              Majster / ekipa przez link może zostać dodany bez e-maila, a dostęp do konkretnego zlecenia wyślesz z poziomu zlecenia.
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

type InvestorDiscoveryContractor = {
  name: string;
  kind: "Firma" | "Majster";
  rating: string;
  reviews: number;
  location: string;
  region: string;
  radius: string;
  status: string;
  tags: string[];
  initials: string;
  accent: string;
  realizations: number;
};

const investorDiscoveryDemo: InvestorDiscoveryContractor[] = [
  {
    name: "CleanPro Remonty",
    kind: "Firma",
    rating: "4,9",
    reviews: 28,
    location: "Warszawa i okolice",
    region: "woj. mazowieckie",
    radius: "Działa w promieniu 40 km",
    status: "Dostępny",
    tags: ["remont-mieszkan", "wykonczenia-wnetrz", "malowanie"],
    initials: "CP",
    accent: "linear-gradient(145deg, #111827, #f59e0b)",
    realizations: 12,
  },
  {
    name: "HydroInstal",
    kind: "Firma",
    rating: "4,8",
    reviews: 17,
    location: "Kraków i okolice",
    region: "woj. małopolskie",
    radius: "Działa w promieniu 60 km",
    status: "Dostępny",
    tags: ["hydraulika", "ogrzewanie", "udraznianie"],
    initials: "HI",
    accent: "linear-gradient(145deg, #e0f2fe, #2563eb)",
    realizations: 8,
  },
  {
    name: "Elektrix Solutions",
    kind: "Firma",
    rating: "4,9",
    reviews: 31,
    location: "Wrocław i okolice",
    region: "woj. dolnośląskie",
    radius: "Działa w promieniu 50 km",
    status: "Dostępny",
    tags: ["elektryka", "fotowoltaika", "wentylacja"],
    initials: "ES",
    accent: "linear-gradient(145deg, #064e3b, #0f172a)",
    realizations: 10,
  },
  {
    name: "Jan Majster",
    kind: "Majster",
    rating: "4,8",
    reviews: 24,
    location: "Łódź i okolice",
    region: "woj. łódzkie",
    radius: "Działa w promieniu 35 km",
    status: "Dostępny",
    tags: ["wykonczenia-wnetrz", "glazura", "bialy-montaz"],
    initials: "JM",
    accent: "linear-gradient(145deg, #f97316, #1f2937)",
    realizations: 6,
  },
];

function serviceTagLabel(slug: string): string {
  return tagBySlug(slug)?.label || slug;
}

function InvestorDiscoveryPage({ notify }: { notify: (toast: Toast) => void }) {
  const [query, setQuery] = useState("");
  const [selectedTags, setSelectedTags] = useState(["remont-mieszkan", "wykonczenia-wnetrz", "elektryka", "hydraulika", "malowanie"]);
  const [tagToAdd, setTagToAdd] = useState("");
  const [city, setCity] = useState("");
  const [region, setRegion] = useState("");
  const [radius, setRadius] = useState("50");
  const [sort, setSort] = useState("Najlepiej oceniani");
  const futureMessage = "Moduł publicznych profili wykonawców jest w przygotowaniu.";
  const availableTags = filterServiceTags(query, selectedTags).slice(0, 12);

  function addTag(slug = tagToAdd) {
    if (!slug || selectedTags.includes(slug)) return;
    setSelectedTags((current) => [...current, slug]);
    setTagToAdd("");
    setQuery("");
  }

  function clearFilters() {
    setQuery("");
    setSelectedTags([]);
    setTagToAdd("");
    setCity("");
    setRegion("");
    setRadius("50");
    setSort("Najlepiej oceniani");
  }

  return (
    <div className="page investor-discovery-page">
      <header className="page-header investor-discovery-header">
        <div>
          <span className="eyebrow">Moduł przyszłościowy</span>
          <h1>Wyszukaj wykonawcę</h1>
          <p>Znajdź sprawdzonych wykonawców i firmy po specjalizacji oraz obszarze działania.</p>
        </div>
        <aside className="future-module-note">
          <span><Icon name="report" /></span>
          <div>
            <strong>To moduł przyszłościowy</strong>
            <small>Publiczne profile wykonawców będą widoczne po wdrożeniu profili i portfolio.</small>
          </div>
        </aside>
      </header>

      <section className="investor-discovery-filters">
        <div className="discovery-filter-group discovery-filter-group--wide">
          <label>Specjalizacje</label>
          <div className="discovery-search-input">
            <Icon name="search" size={18} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Wyszukaj specjalizację..."
            />
            <select value={tagToAdd} onChange={(event) => setTagToAdd(event.target.value)}>
              <option value="">Wybierz z listy</option>
              {availableTags.map((tag) => (
                <option key={tag.slug} value={tag.slug}>{tag.label}</option>
              ))}
            </select>
          </div>
          <div className="service-chip-list">
            {selectedTags.map((slug) => (
              <button type="button" key={slug} onClick={() => setSelectedTags((current) => current.filter((item) => item !== slug))}>
                {serviceTagLabel(slug)} <Icon name="close" size={12} />
              </button>
            ))}
            <button type="button" className="service-chip-add" onClick={() => addTag(tagToAdd || availableTags[0]?.slug)}>
              <Icon name="plus" size={14} /> Dodaj specjalizację
            </button>
          </div>
        </div>

        <div className="discovery-filter-group">
          <label>Obszar działania</label>
          <div className="discovery-location-grid">
            <input value={city} onChange={(event) => setCity(event.target.value)} placeholder="Miasto lub miejscowość" />
            <select value={region} onChange={(event) => setRegion(event.target.value)}>
              <option value="">Województwo / obszar</option>
              <option>mazowieckie</option>
              <option>małopolskie</option>
              <option>dolnośląskie</option>
              <option>łódzkie</option>
              <option>warmińsko-mazurskie</option>
            </select>
            <select value={radius} onChange={(event) => setRadius(event.target.value)}>
              <option value="10">Promień działania: 10 km</option>
              <option value="25">Promień działania: 25 km</option>
              <option value="50">Promień działania: 50 km</option>
              <option value="100">Promień działania: 100 km</option>
            </select>
          </div>
        </div>

        <footer>
          <Button type="button" variant="secondary" onClick={clearFilters}>Wyczyść filtry</Button>
          <Button type="button" onClick={() => notify({ kind: "info", message: futureMessage })}>Szukaj wykonawców</Button>
        </footer>
      </section>

      <div className="discovery-results-bar">
        <p>Znaleziono 24 wykonawców</p>
        <label>Sortuj:
          <select value={sort} onChange={(event) => setSort(event.target.value)}>
            <option>Najlepiej oceniani</option>
            <option>Najwięcej realizacji</option>
            <option>Najbliżej</option>
            <option>Najnowsze profile</option>
          </select>
        </label>
      </div>

      <section className="contractor-discovery-list" aria-label="Wyniki wyszukiwania wykonawców">
        {investorDiscoveryDemo.map((contractor) => (
          <article className="contractor-discovery-card" key={contractor.name}>
            <div className="contractor-discovery-avatar" style={{ background: contractor.accent }}>{contractor.initials}</div>
            <div className="contractor-discovery-main">
              <h2>{contractor.name} <span title="Profil przyszłościowy"><Icon name="check" size={15} /></span></h2>
              <p>{contractor.kind} <b><Icon name="star" size={14} /> {contractor.rating}</b> <small>({contractor.reviews} opinii)</small></p>
              <div className="service-chip-list service-chip-list--small">
                {contractor.tags.slice(0, 3).map((slug) => <span key={slug}>{serviceTagLabel(slug)}</span>)}
              </div>
            </div>
            <div className="contractor-discovery-location">
              <strong><Icon name="map-pin" size={16} /> {contractor.location}</strong>
              <small>{contractor.region}</small>
              <span>{contractor.status}</span>
              <p>{contractor.radius}</p>
            </div>
            <div className="contractor-discovery-gallery" aria-label="Wybrane realizacje demo">
              <strong>Wybrane realizacje</strong>
              <div>
                {[0, 1, 2].map((item) => <span key={item} style={{ backgroundImage: `linear-gradient(135deg, rgba(6,37,87,.18), rgba(255,90,0,.22)), linear-gradient(${120 + item * 35}deg, #dbeafe, #f8fafc)` }} />)}
                <em>+{contractor.realizations}</em>
              </div>
            </div>
            <div className="contractor-discovery-actions">
              <Button type="button" onClick={() => notify({ kind: "info", message: "Pełna funkcja profilu pojawi się po wdrożeniu publicznych profili." })}>Zobacz profil</Button>
              <Button type="button" variant="secondary" icon="bookmark" onClick={() => notify({ kind: "info", message: futureMessage })}>Zapisz wykonawcę</Button>
            </div>
          </article>
        ))}
      </section>
      <p className="future-module-footer">To moduł przyszłościowy. Funkcjonalność w przygotowaniu.</p>
    </div>
  );
}

function InvestorPostJobPage({ notify }: { notify: (toast: Toast) => void }) {
  const [title, setTitle] = useState("");
  const [location, setLocation] = useState("");
  const [budget, setBudget] = useState("");
  const [term, setTerm] = useState("");
  const [description, setDescription] = useState("");
  const [selectedTags, setSelectedTags] = useState(["remont-mieszkan", "hydraulika"]);
  const [tagToAdd, setTagToAdd] = useState("");
  const [target, setTarget] = useState<"Firma" | "Majster" | "Bez znaczenia">("Firma");
  const futureMessage = "Publikowanie zleceń będzie dostępne po wdrożeniu modułu zleceń w okolicy.";
  const availableTags = serviceTags.filter((tag) => !selectedTags.includes(tag.slug));

  function addTag() {
    if (!tagToAdd) return;
    setSelectedTags((current) => [...current, tagToAdd]);
    setTagToAdd("");
  }

  function submitFutureAction(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    notify({ kind: "info", message: futureMessage });
  }

  return (
    <div className="page investor-post-job-page">
      <header className="page-header investor-discovery-header">
        <div>
          <span className="eyebrow">Moduł przyszłościowy</span>
          <h1>Ogłoś zlecenie</h1>
          <p>Opublikuj zlecenie, aby firmy i majstrowie mogli je znaleźć.</p>
        </div>
      </header>
      <div className="post-job-layout">
        <form className="post-job-form" onSubmit={submitFutureAction}>
          <section>
            <header><span>1</span><h2>Podstawowe informacje</h2></header>
            <div className="post-job-grid">
              <label>Nazwa zlecenia<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Np. Remont łazienki 6 m²" /></label>
              <label>Lokalizacja<input value={location} onChange={(event) => setLocation(event.target.value)} placeholder="Miasto lub dzielnica" /></label>
              <label>Budżet<select value={budget} onChange={(event) => setBudget(event.target.value)}><option value="">Wybierz budżet</option><option>do 5 000 zł</option><option>5 000 - 10 000 zł</option><option>10 000 - 15 000 zł</option><option>15 000 - 30 000 zł</option><option>powyżej 30 000 zł</option></select></label>
              <label>Planowany termin<input value={term} onChange={(event) => setTerm(event.target.value)} placeholder="Np. Czerwiec 2026" /></label>
            </div>
          </section>

          <section>
            <header><span>2</span><h2>Zakres prac</h2></header>
            <label>Opis zlecenia<textarea rows={4} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Opisz szczegółowo, jakie prace mają zostać wykonane..." /></label>
            <div className="service-chip-list post-job-tags">
              {selectedTags.map((slug) => (
                <button type="button" key={slug} onClick={() => setSelectedTags((current) => current.filter((item) => item !== slug))}>
                  {serviceTagLabel(slug)} <Icon name="close" size={12} />
                </button>
              ))}
              <select value={tagToAdd} onChange={(event) => setTagToAdd(event.target.value)}>
                <option value="">Dodaj specjalizację</option>
                {availableTags.map((tag) => <option key={tag.slug} value={tag.slug}>{tag.label}</option>)}
              </select>
              <Button type="button" variant="secondary" icon="plus" onClick={addTag} disabled={!tagToAdd}>Dodaj</Button>
            </div>
          </section>

          <section>
            <header><span>3</span><h2>Kogo szukasz</h2></header>
            <div className="post-job-segments" role="group" aria-label="Kogo szukasz">
              {(["Firma", "Majster", "Bez znaczenia"] as const).map((option) => (
                <button type="button" className={target === option ? "active" : ""} onClick={() => setTarget(option)} key={option}>
                  <Icon name={option === "Firma" ? "building" : option === "Majster" ? "users" : "search"} size={18} /> {option}
                </button>
              ))}
            </div>
          </section>

          <section>
            <header><span>4</span><h2>Widoczność i publikacja</h2></header>
            <div className="post-job-status">
              <p><Icon name="report" size={18} /> Status: <strong>Szkic</strong></p>
              <p><Icon name="check" size={18} /> Po publikacji zlecenie trafi do modułu Zlecenia w okolicy dla wykonawców. Wykonawcy będą mogli znaleźć je po lokalizacji, specjalizacji i własnych filtrach.</p>
            </div>
          </section>

          <footer>
            <Button type="button" variant="secondary" onClick={() => notify({ kind: "info", message: futureMessage })}>Zapisz szkic</Button>
            <Button type="submit">Opublikuj zlecenie</Button>
          </footer>
        </form>

        <aside className="post-job-preview">
          <article>
            <h2>Podgląd publikacji</h2>
            <div className="post-preview-card">
              <span className="status status--assigned">Szkic</span>
              <h3>{title || "Remont łazienki w mieszkaniu"}</h3>
              <p><Icon name="map-pin" size={16} /> {location || "Ełk, warmińsko-mazurskie"}</p>
              <div className="post-preview-tags">
                <span>{budget || "10 000 - 15 000 zł"}</span>
                <span>{term || "Czerwiec 2026"}</span>
              </div>
              <div className="service-chip-list service-chip-list--small">
                {selectedTags.slice(0, 4).map((slug) => <span key={slug}>{serviceTagLabel(slug)}</span>)}
              </div>
              <p>Szukam: <strong>{target}</strong></p>
              <Button type="button" onClick={() => notify({ kind: "info", message: futureMessage })}>Zobacz zlecenie</Button>
            </div>
          </article>
          <article className="post-job-info">
            <span><Icon name="report" /></span>
            <div>
              <h2>Dla inwestora</h2>
              <p>Po publikacji wykonawcy znajdą to zlecenie w sekcji Zlecenia w okolicy.</p>
            </div>
          </article>
        </aside>
      </div>
    </div>
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
      <div className="page company-owner-team-page">
        <header className="page-header">
          <div>
            <span className="eyebrow">Zespół firmy</span>
            <h1>{peopleLabels.section}</h1>
            <p>Zarządzaj majstrami, ekipami, dostępami i przypisanymi zleceniami.</p>
          </div>
        </header>
        <CompanyTeamPanel workspaceId={user.workspaces[0].id} projects={projects} onOpenProject={onProject} onChanged={refreshUser} notify={notify} />
      </div>
    );
  }
  if (user.profile_type === "investor" && user.workspaces.length > 0) {
    return (
      <div className="page worker-home worker-home--investor investor-contractors-page">
        <WorkerMobileHeader title="Inwestor" />
        <header className="worker-page-header">
          <div className="worker-title-row">
            <div>
              <span className="eyebrow">Współpraca</span>
              <h1>Wykonawcy</h1>
              <p>Zarządzaj wykonawcami przypisanymi do Twoich inwestycji.</p>
            </div>
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
  const [workerProfileEditorOpen, setWorkerProfileEditorOpen] = useState(false);
  useEffect(() => {
    if (user.is_admin) api("/admin/overview").then(setAdmin).catch(() => null);
  }, [user.is_admin]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const payload: Record<string, FormDataEntryValue | string> = { locale: "pl" };
    for (const key of ["name", "phone", "preferred_mode", "public_profile_name"]) {
      const value = data.get(key);
      if (typeof value === "string") payload[key] = value;
    }
    try {
      const updated = await api<User>("/me", {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      onUpdated(updated);
      notify({ kind: "success", message: "Profil zapisany" });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zapisać profilu" });
    }
  }
  if (isCompanyOwner(user)) {
    const primaryWorkspace = user.workspaces[0];
    const displayName = user.name || user.email || "Szef firmy";
    const displayEmail = user.email || "Konto bez e-maila";
    const displayPhone = user.phone || "Nie podano";
    const defaultModeLabel = uiMode === "simple" ? "Prosty" : "Rozbudowany";
    return (
      <div className="page worker-settings-page company-owner-settings-page">
        <WorkerMobileHeader title="Szef firmy" />
        <header className="worker-page-header">
          <div>
            <span className="eyebrow">Konto firmy</span>
            <h1>Ustawienia</h1>
            <p>Dane szefa firmy, podstawowe informacje o firmie i szybkie odnośniki do zespołu.</p>
          </div>
        </header>
        <section className="worker-settings-stack company-owner-settings-stack">
          <article className="worker-settings-card company-owner-settings-card">
            <h2>Moje konto</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row">
                <span><Icon name="users" /></span>
                <div><strong>Imię i nazwisko</strong><small>{displayName}</small></div>
                <b>{displayName}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="send" /></span>
                <div><strong>E-mail / login</strong><small>{displayEmail}</small></div>
                <b>{displayEmail}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="phone" /></span>
                <div><strong>Telefon</strong><small>{displayPhone}</small></div>
                <b>{displayPhone}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="home" /></span>
                <div><strong>Typ konta</strong><small>Właściciel firmy</small></div>
                <b>Szef firmy</b>
              </div>
            </div>
            <form className="worker-settings-edit-form company-owner-profile-form" onSubmit={submit}>
              <label>Imię i nazwisko<input name="name" defaultValue={user.name} placeholder="Jan Kowalski" /></label>
              <label>Telefon<input name="phone" defaultValue={user.phone} placeholder="+48 600 000 000" /></label>
              <div className="worker-settings-email-lock">
                <strong>E-mail / login</strong>
                <p>Obecnie e-mail jest loginem do konta. Login pozostaje bez zmian: <u>{displayEmail}</u>.</p>
                <small>Zmiana e-maila lub loginu zostaje osobnym krokiem auth/model.</small>
              </div>
              <input type="hidden" name="preferred_mode" value={uiMode === "simple" ? "field" : "expanded"} />
              <Button type="submit">Zapisz profil</Button>
            </form>
          </article>

          <article className="worker-settings-card company-owner-settings-card">
            <h2>Dane firmy</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row">
                <span><Icon name="home" /></span>
                <div><strong>Firma</strong><small>{primaryWorkspace?.name || "Brak przypisanej firmy"}</small></div>
                <b>{primaryWorkspace?.name || "Brak"}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="settings" /></span>
                <div><strong>Rola w workspace</strong><small>{primaryWorkspace?.role || "owner"}</small></div>
                <b>Owner</b>
              </div>
              <div className="worker-settings-row worker-settings-row--disabled">
                <span><Icon name="clipboard" /></span>
                <div><strong>Edycja danych firmy</strong><small>Pełne dane firmy edytujesz w sekcji Majstrowie i ekipy.</small></div>
                <b>Panel zespołu</b>
              </div>
            </div>
          </article>

          <article className="worker-settings-card company-owner-settings-card">
            <h2>Zespół i dostępy</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row">
                <span><Icon name="users" /></span>
                <div><strong>Majstrowie i ekipy</strong><small>Zarządzanie pracownikami, ekipami i wykonawcami link-only.</small></div>
                <b>Aktywne</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="link" /></span>
                <div><strong>Link dla majstra / ekipy</strong><small>Dostęp do konkretnego zlecenia przez link /g.</small></div>
                <b>Per zlecenie</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="clipboard" /></span>
                <div><strong>Przypisane zlecenia</strong><small>Widoczne przy podglądzie majstra lub ekipy.</small></div>
                <b>W panelu ludzi</b>
              </div>
            </div>
          </article>

          <article className="worker-settings-card company-owner-settings-card">
            <h2>Linki i widoczność</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row">
                <span><Icon name="link" /></span>
                <div><strong>Link klienta</strong><small>Publiczny podgląd /c dla klienta zlecenia.</small></div>
                <b>W zleceniu</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="report" /></span>
                <div><strong>Raporty PDF</strong><small>Raporty dostępne z poziomu zlecenia i sekcji Raporty.</small></div>
                <b>Dostępne</b>
              </div>
              <div className="worker-settings-row worker-settings-row--disabled">
                <span><Icon name="image" /></span>
                <div><strong>Portfolio firmy</strong><small>Publiczne portfolio firmy nie jest częścią tego kroku.</small></div>
                <b>Future-only</b>
              </div>
            </div>
          </article>

          <article className="worker-settings-card company-owner-settings-card">
            <h2>Konto</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row worker-settings-row--mode">
                <span><Icon name="settings" /></span>
                <div>
                  <strong>Tryb widoku</strong>
                  <small>Przełącznik Prosty / Rozbudowany w widokach zleceń firmy.</small>
                  <WorkerModeSwitch uiMode={uiMode} onUiModeChange={onUiModeChange} />
                </div>
                <b>{defaultModeLabel}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="check" /></span>
                <div><strong>Status konta</strong><small>Konto właściciela firmy jest aktywne.</small></div>
                <b>Aktywne</b>
              </div>
            </div>
            <button className="worker-settings-danger-row" type="button" onClick={onLogout}>
              <span><Icon name="back" /></span>
              <strong>Wyloguj się</strong>
            </button>
          </article>
        </section>
      </div>
    );
  }
  if (isInvestor(user)) {
    const displayName = user.name || user.email || "Inwestor";
    const displayEmail = user.email || "Konto bez e-maila";
    const displayPhone = user.phone || "Nie podano";
    const defaultModeLabel = uiMode === "simple" ? "Prosty" : "Rozbudowany";
    return (
      <div className="page worker-settings-page investor-settings-page">
        <WorkerMobileHeader title="Inwestor" />
        <header className="worker-page-header">
          <div>
            <span className="eyebrow">Konto inwestora</span>
            <h1>Ustawienia</h1>
            <p>Prosty profil inwestora używany w inwestycjach i kontaktach z wykonawcami.</p>
          </div>
        </header>
        <section className="worker-settings-stack investor-settings-stack">
          <article className="worker-settings-card investor-settings-card">
            <h2>Moje konto</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row">
                <span><Icon name="users" /></span>
                <div><strong>Imię / nazwa</strong><small>{displayName}</small></div>
                <b>{displayName}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="send" /></span>
                <div><strong>E-mail / login</strong><small>{displayEmail}</small></div>
                <b>{displayEmail}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="phone" /></span>
                <div><strong>Telefon</strong><small>{displayPhone}</small></div>
                <b>{displayPhone}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="home" /></span>
                <div><strong>Typ konta</strong><small>Prywatny panel inwestora</small></div>
                <b>Inwestor</b>
              </div>
            </div>
          </article>

          <article className="worker-settings-card investor-settings-card">
            <h2>Profil inwestora</h2>
            <p className="investor-settings-copy">Te dane są używane w Twoich inwestycjach i kontaktach z wykonawcami.</p>
            <form className="worker-settings-edit-form investor-profile-form" onSubmit={submit}>
              <label>Nazwa wyświetlana<input name="name" defaultValue={user.name} placeholder="Jan Kowalski" /></label>
              <label>Telefon kontaktowy<input name="phone" defaultValue={user.phone} placeholder="+48 600 000 000" /></label>
              <div className="worker-settings-email-lock">
                <strong>E-mail / login</strong>
                <p>Obecnie e-mail jest loginem do konta. Login pozostaje bez zmian: <u>{displayEmail}</u>.</p>
                <small>Zmiana e-maila lub loginu będzie osobnym krokiem auth/model.</small>
              </div>
              <input type="hidden" name="preferred_mode" value={uiMode === "simple" ? "field" : "expanded"} />
              <Button type="submit">Zapisz profil</Button>
            </form>
          </article>

          <article className="worker-settings-card investor-settings-card">
            <h2>Zakres panelu</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row">
                <span><Icon name="clipboard" /></span>
                <div><strong>Inwestycje i zlecenia</strong><small>Lista prac zleconych wykonawcom.</small></div>
                <b>Aktywne</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="users" /></span>
                <div><strong>Wykonawcy</strong><small>Prywatna lista wykonawców i dostępów link-only.</small></div>
                <b>Prywatne</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="report" /></span>
                <div><strong>Raporty</strong><small>Raporty i wpisy z Twoich inwestycji.</small></div>
                <b>Dostępne</b>
              </div>
              <div className="worker-settings-row worker-settings-row--disabled">
                <span><Icon name="link" /></span>
                <div><strong>Marketplace</strong><small>Panel inwestora służy do prywatnego zarządzania zlecanymi pracami. Nie jest publiczną bazą firm ani marketplace.</small></div>
                <b>Nieaktywne</b>
              </div>
            </div>
          </article>

          <article className="worker-settings-card investor-settings-card">
            <h2>Konto</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row worker-settings-row--mode">
                <span><Icon name="settings" /></span>
                <div>
                  <strong>Tryb widoku</strong>
                  <small>Ten sam przełącznik Prosty / Rozbudowany co w inwestycjach.</small>
                  <WorkerModeSwitch uiMode={uiMode} onUiModeChange={onUiModeChange} />
                </div>
                <b>{defaultModeLabel}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="check" /></span>
                <div><strong>Status konta</strong><small>Konto inwestora jest aktywne.</small></div>
                <b>Aktywne</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="home" /></span>
                <div><strong>Typ konta</strong><small>Panel do zarządzania inwestycjami, wykonawcami i raportami.</small></div>
                <b>Inwestor</b>
              </div>
            </div>
            <button className="worker-settings-danger-row investor-settings-logout" type="button" onClick={onLogout}>
              <span><Icon name="back" /></span>
              <strong>Wyloguj się</strong>
            </button>
          </article>
        </section>
      </div>
    );
  }
  if (isCompanyWorker(user)) {
    const assignedWorkspace = user.workspaces[0];
    const displayName = user.name || user.email || "Majster firmy";
    const displayEmail = user.email || "Konto dostępowe bez e-maila";
    const displayPhone = user.phone || "Nie podano";
    const defaultModeLabel = uiMode === "simple" ? "Prosty" : "Rozbudowany";
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
            <div className="worker-settings-rows">
              <div className="worker-settings-row">
                <span><Icon name="users" /></span>
                <div><strong>Imię i nazwisko</strong><small>{displayName}</small></div>
                <b>{displayName}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="send" /></span>
                <div><strong>E-mail / login</strong><small>{displayEmail}</small></div>
                <b>{displayEmail}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="settings" /></span>
                <div><strong>Telefon</strong><small>{displayPhone}</small></div>
                <b>{displayPhone}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="clipboard" /></span>
                <div><strong>Typ konta</strong><small>Majster firmy</small></div>
                <b>Majster firmy</b>
              </div>
              <button
                className="worker-settings-row worker-settings-row--action"
                type="button"
                aria-expanded={workerProfileEditorOpen}
                onClick={() => setWorkerProfileEditorOpen((open) => !open)}
              >
                <span><Icon name="settings" /></span>
                <div><strong>Edytuj dane</strong><small>{workerProfileEditorOpen ? "Ukryj formularz profilu" : "Zmień imię i telefon"}</small></div>
                <em>{workerProfileEditorOpen ? "Ukryj" : "Edytuj"}</em>
              </button>
            </div>
            {workerProfileEditorOpen && (
              <form className="worker-settings-edit-form" onSubmit={submit}>
                <label>Imię i nazwisko<input name="name" defaultValue={user.name} placeholder="Jan Kowalski" /></label>
                <label>Telefon<input name="phone" defaultValue={user.phone} placeholder="+48 600 000 000" /></label>
                <div className="worker-settings-email-lock">
                  <strong>E-mail / login</strong>
                  <p>
                    Obecnie e-mail jest loginem do konta. Login pozostaje bez zmian:
                    {" "}<u>{displayEmail}</u>.
                  </p>
                  <small>Zmiana e-maila kontaktowego i osobna zmiana loginu wymagają kroku auth/model 10B/10C.</small>
                </div>
                <input type="hidden" name="preferred_mode" value={uiMode === "simple" ? "field" : "expanded"} />
                <Button type="submit">Zapisz profil</Button>
              </form>
            )}
          </article>
          <article className="worker-settings-card">
            <h2>Przypisana firma</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row">
                <span><Icon name="users" /></span>
                <div>
                  <strong>Firma</strong>
                  <small>{assignedWorkspace?.name || "Brak przypisanej firmy"}</small>
                </div>
                <b>{assignedWorkspace?.name || "Brak"}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="clipboard" /></span>
                <div>
                  <strong>Rola w firmie</strong>
                  <small>{assignedWorkspace?.role ? "Konto przypisane przez szefa firmy" : "Firma pojawi się tutaj po przypisaniu."}</small>
                </div>
                <b>Majster firmy</b>
              </div>
            </div>
          </article>
          <article className="worker-settings-card">
            <h2>Preferencje</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row worker-settings-row--mode">
                <span><Icon name="settings" /></span>
                <div>
                  <strong>Tryb domyślny</strong>
                  <small>Tryb uruchamiany po zalogowaniu.</small>
                  <WorkerModeSwitch uiMode={uiMode} onUiModeChange={onUiModeChange} />
                </div>
                <b>{defaultModeLabel}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="send" /></span>
                <div><strong>Język aplikacji</strong><small>Ustawiony dla konta</small></div>
                <b>{user.locale === "pl" ? "Polski" : user.locale || "Polski"}</b>
              </div>
            </div>
          </article>
          <article className="worker-settings-card">
            <h2>Bezpieczeństwo</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row worker-settings-row--disabled">
                <span><Icon name="settings" /></span>
                <div><strong>Zmiana hasła</strong><small>Dostępne później</small></div>
                <b>KROK 10B/10C</b>
              </div>
              <div className="worker-settings-row worker-settings-row--disabled">
                <span><Icon name="link" /></span>
                <div><strong>Kod dostępu</strong><small>Aktywacja linkiem będzie osobnym krokiem.</small></div>
                <b>KROK 10B/10C</b>
              </div>
            </div>
          </article>
          <button className="worker-settings-danger-row" type="button" onClick={onLogout}>
            <span><Icon name="back" /></span>
            <strong>Wyloguj się</strong>
          </button>
        </section>
      </div>
    );
  }
  if (isIndependentContractor(user)) {
    const displayName = user.name || user.email || "Samodzielny majster";
    const publicProfileName = user.public_profile_name || displayName;
    const displayEmail = user.email || "Konto dostępowe bez e-maila";
    const displayPhone = user.phone || "Nie podano";
    const defaultModeLabel = uiMode === "simple" ? "Prosty" : "Rozbudowany";
    return (
      <div className="page worker-settings-page">
        <WorkerMobileHeader />
        <header className="worker-page-header">
          <div>
            <span className="eyebrow">Konto wykonawcy</span>
            <h1>Ustawienia</h1>
            <p>Proste dane samodzielnego majstra. Bez panelu firmy, ludzi i ekip.</p>
          </div>
        </header>
        <section className="worker-settings-stack">
          <article className="worker-settings-card">
            <h2>Moje konto</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row">
                <span><Icon name="users" /></span>
                <div><strong>Imię i nazwisko</strong><small>{displayName}</small></div>
                <b>{displayName}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="send" /></span>
                <div><strong>E-mail / login</strong><small>{displayEmail}</small></div>
                <b>{displayEmail}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="settings" /></span>
                <div><strong>Telefon</strong><small>{displayPhone}</small></div>
                <b>{displayPhone}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="clipboard" /></span>
                <div><strong>Typ konta</strong><small>Samodzielny majster</small></div>
                <b>Samodzielny majster</b>
              </div>
              <button
                className="worker-settings-row worker-settings-row--action"
                type="button"
                aria-expanded={workerProfileEditorOpen}
                onClick={() => setWorkerProfileEditorOpen((open) => !open)}
              >
                <span><Icon name="settings" /></span>
                <div><strong>Edytuj dane</strong><small>{workerProfileEditorOpen ? "Ukryj formularz profilu" : "Zmień imię i telefon"}</small></div>
                <em>{workerProfileEditorOpen ? "Ukryj" : "Edytuj"}</em>
              </button>
            </div>
            {workerProfileEditorOpen && (
              <form className="worker-settings-edit-form" onSubmit={submit}>
                <label>Imię i nazwisko<input name="name" defaultValue={user.name} placeholder="Jan Kowalski" /></label>
                <label>Telefon<input name="phone" defaultValue={user.phone} placeholder="+48 600 000 000" /></label>
                <div className="worker-settings-email-lock">
                  <strong>E-mail / login</strong>
                  <p>
                    Obecnie e-mail jest loginem do konta. Login pozostaje bez zmian:
                    {" "}<u>{displayEmail}</u>.
                  </p>
                  <small>Zmiana e-maila kontaktowego i osobna zmiana loginu będą dostępne w kroku 10B/10C.</small>
                </div>
                <input type="hidden" name="preferred_mode" value={uiMode === "simple" ? "field" : "expanded"} />
                <Button type="submit">Zapisz profil</Button>
              </form>
            )}
          </article>
          <article className="worker-settings-card">
            <h2>Profil wykonawcy</h2>
            <form className="worker-profile-name-form" onSubmit={submit}>
              <label>
                Nazwa profilu
                <input name="public_profile_name" defaultValue={user.public_profile_name || ""} placeholder="np. Remonty Kowalski" />
              </label>
              <p>Widoczna dla klienta w linku klienta, raportach PDF i podglądach robót.</p>
              <Button type="submit">Zapisz</Button>
            </form>
            <div className="worker-settings-rows">
              <div className="worker-settings-row">
                <span><Icon name="clipboard" /></span>
                <div>
                  <strong>Nazwa profilu</strong>
                  <small>Widoczna dla klienta w linku klienta, raportach PDF i podglądach robót.</small>
                </div>
                <b>{publicProfileName}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="users" /></span>
                <div>
                  <strong>Domyślna rola</strong>
                  <small>Samodzielny wykonawca bez przypisanej firmy.</small>
                </div>
                <b>Samodzielny majster</b>
              </div>
              <div className="worker-settings-row worker-settings-row--disabled">
                <span><Icon name="image" /></span>
                <div>
                  <strong>Profil wykonawcy</strong>
                  <small>Profil wykonawcy będzie bazą pod portfolio i publiczną wizytówkę.</small>
                </div>
                <b>Dostępne później</b>
              </div>
            </div>
          </article>
          <article className="worker-settings-card">
            <h2>Preferencje</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row worker-settings-row--mode">
                <span><Icon name="settings" /></span>
                <div>
                  <strong>Tryb domyślny</strong>
                  <small>Tryb uruchamiany po zalogowaniu.</small>
                  <WorkerModeSwitch uiMode={uiMode} onUiModeChange={onUiModeChange} />
                </div>
                <b>{defaultModeLabel}</b>
              </div>
              <div className="worker-settings-row">
                <span><Icon name="send" /></span>
                <div><strong>Język aplikacji</strong><small>Ustawiony dla konta</small></div>
                <b>{user.locale === "pl" ? "Polski" : user.locale || "Polski"}</b>
              </div>
            </div>
          </article>
          <article className="worker-settings-card">
            <h2>Bezpieczeństwo</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row">
                <span><Icon name="send" /></span>
                <div><strong>E-mail / login</strong><small>{displayEmail}</small></div>
                <b>{displayEmail}</b>
              </div>
              <div className="worker-settings-row worker-settings-row--disabled">
                <span><Icon name="settings" /></span>
                <div><strong>Zmiana hasła</strong><small>Dostępne w kroku 10B/10C</small></div>
                <b>KROK 10B/10C</b>
              </div>
              <div className="worker-settings-row worker-settings-row--disabled">
                <span><Icon name="link" /></span>
                <div><strong>Kod dostępu</strong><small>Dostępne w kroku 10B/10C</small></div>
                <b>KROK 10B/10C</b>
              </div>
            </div>
          </article>
          <article className="worker-settings-card">
            <h2>Portfolio / wizytówka</h2>
            <div className="worker-settings-rows">
              <div className="worker-settings-row worker-settings-row--disabled">
                <span><Icon name="image" /></span>
                <div><strong>Publiczna wizytówka</strong><small>Portfolio i publiczna wizytówka będą dostępne później.</small></div>
                <b>Dostępne później</b>
              </div>
              <div className="worker-settings-row worker-settings-row--disabled">
                <span><Icon name="clipboard" /></span>
                <div><strong>Realizacje w portfolio</strong><small>Lista publicznych realizacji zostanie podpięta w osobnym kroku.</small></div>
                <b>Future-only</b>
              </div>
              <div className="worker-settings-row worker-settings-row--disabled">
                <span><Icon name="link" /></span>
                <div><strong>Adres strony portfolio</strong><small>Adres publicznej wizytówki nie jest jeszcze aktywny.</small></div>
                <b>Dostępne później</b>
              </div>
            </div>
          </article>
          <button className="worker-settings-danger-row" type="button" onClick={onLogout}>
            <span><Icon name="back" /></span>
            <strong>Wyloguj się</strong>
          </button>
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
  const [audioErrors, setAudioErrors] = useState<string[]>([]);
  const [commentDrafts, setCommentDrafts] = useState<Record<string, string>>({});
  const [commentBusy, setCommentBusy] = useState<Record<string, boolean>>({});
  const [commentErrors, setCommentErrors] = useState<Record<string, string>>({});
  const [commentSuccess, setCommentSuccess] = useState<Record<string, string>>({});

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
    return (
      <div className="public-page">
        <Logo />
        <form className="pin-card" onSubmit={(event) => { event.preventDefault(); void load(pin); }}>
          <Icon name="clipboard" size={42} />
          <h1>Zlecenie chronione</h1>
          <p>Wpisz PIN otrzymany od osoby prowadzącej zlecenie.</p>
          <input value={pin} onChange={(event) => setPin(event.target.value)} inputMode="numeric" autoFocus />
          <Button type="submit">Otwórz zlecenie</Button>
          {error && <p className="form-error">{error}</p>}
        </form>
      </div>
    );
  }
  if (!data?.project) return <div className="public-page"><Logo /><div className="loading-screen">{error || "Ładowanie zlecenia..."}</div></div>;

  const project = data.project as Project;
  const entries = data.entries as Entry[];
  const reports = data.reports as Report[];
  const clientCoverMedia = data.client_cover_media as MediaAsset | null | undefined;
  const withPin = (url: string) => `${url}${pin ? `?pin=${encodeURIComponent(pin)}` : ""}`;
  const { completedCount, progress } = projectStageProgress(project);
  const stagesCount = project.stages?.length || 0;
  const formatter = new Intl.DateTimeFormat("pl", { day: "2-digit", month: "2-digit", year: "numeric" });
  const timeFormatter = new Intl.DateTimeFormat("pl", { dateStyle: "medium", timeStyle: "short" });
  const historyEntries = [...entries].sort((left, right) => {
    const result = new Date(left.occurred_at).getTime() - new Date(right.occurred_at).getTime();
    return result || left.id.localeCompare(right.id);
  });
  const latestDate = historyEntries.at(-1)?.occurred_at || project.updated_at || project.created_at;
  const heroImage = clientCoverMedia || historyEntries
    .flatMap((entry) => entry.media.filter((asset) => asset.kind === "image"))
    .at(-1);
  const contractorName = project.public_contractor_name || "Wykonawca";
  const safeStatusLabel = statusLabels[project.status] || project.status;
  const formatDate = (value?: string | null) => value ? formatter.format(new Date(value)) : "Nie ustawiono";
  const markAudioError = (assetId: string) => {
    setAudioErrors((current) => current.includes(assetId) ? current : [...current, assetId]);
  };
  const updatePublicEntry = (updatedEntry: Entry) => {
    setData((current: any) => current ? {
      ...current,
      entries: (current.entries as Entry[]).map((entry) => entry.id === updatedEntry.id ? updatedEntry : entry),
    } : current);
  };
  const submitClientComment = async (entry: Entry, intent: CommentIntent = "comment") => {
    const draft = (commentDrafts[entry.id] || "").trim();
    if (intent === "comment" && !draft) {
      setCommentErrors((current) => ({ ...current, [entry.id]: "Wpisz komentarz do tego wpisu." }));
      return;
    }
    setCommentBusy((current) => ({ ...current, [entry.id]: true }));
    setCommentErrors((current) => ({ ...current, [entry.id]: "" }));
    setCommentSuccess((current) => ({ ...current, [entry.id]: "" }));
    try {
      const updatedEntry = await api<Entry>(
        `/public/projects/${token}/entries/${entry.id}/comments${pin ? `?pin=${encodeURIComponent(pin)}` : ""}`,
        {
          method: "POST",
          body: JSON.stringify({ body: draft, intent }),
        },
      );
      updatePublicEntry(updatedEntry);
      setCommentDrafts((current) => ({ ...current, [entry.id]: "" }));
      setCommentSuccess((current) => ({ ...current, [entry.id]: "Komentarz dodany." }));
    } catch {
      setCommentErrors((current) => ({ ...current, [entry.id]: "Nie udało się dodać komentarza. Spróbuj ponownie." }));
    } finally {
      setCommentBusy((current) => ({ ...current, [entry.id]: false }));
    }
  };

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
                <img src={withPin(heroImage.url)} alt={heroImage.original_name || "Zdjęcie główne zlecenia"} loading="lazy" />
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

        <section className="client-section-card client-history-card">
          <span className="client-section-icon client-section-icon--green"><Icon name="check" /></span>
          <div>
            <h2>Historia prac</h2>
            {historyEntries.length === 0 ? (
              <p className="client-muted">Pierwsze wpisy postępu pojawią się tutaj po dodaniu aktualizacji.</p>
            ) : (
              <div className="client-history-list">
                {historyEntries.map((entry) => {
                  const imageAssets = entry.media.filter((asset) => asset.kind === "image");
                  const audioAssets = entry.media.filter((asset) => asset.kind === "audio");
                  const title = entry.kind === "problem"
                    ? "Problem"
                    : imageAssets.length > 0
                      ? "Zdjęcia postępu"
                      : audioAssets.length > 0
                        ? "Opis głosowy"
                        : "Aktualizacja";
                  const author = entry.author_label || entry.author?.name || entry.guest_label || "Wykonawca";
                  return (
                    <article className={`client-history-entry client-history-entry--${entry.kind}`} key={entry.id}>
                      <header>
                        <div>
                          <span><Icon name={entry.kind === "problem" ? "alert" : imageAssets.length ? "camera" : audioAssets.length ? "mic" : "clipboard"} /></span>
                          <div>
                            <h3>{title}</h3>
                            <small>{timeFormatter.format(new Date(entry.occurred_at))} · {author}</small>
                          </div>
                        </div>
                        {entry.kind === "problem" && <em>{entry.problem_status === "resolved" ? "Rozwiązany" : "Do rozwiązania"}</em>}
                      </header>
                      {(entry.body || entry.transcript) && (
                        <div className="client-history-entry__text">
                          {entry.body && <p>{entry.body}</p>}
                          {entry.transcript && entry.transcript !== entry.body && <blockquote>{entry.transcript}</blockquote>}
                        </div>
                      )}
                      {imageAssets.length > 0 && (
                        <div className="client-history-entry__media">
                          {imageAssets.map((asset) => {
                            const src = withPin(asset.url);
                            return (
                              <button type="button" className="media-button" onClick={() => setLightbox({ src, alt: asset.original_name })} key={asset.id}>
                                <img src={src} alt={asset.original_name || "Zdjęcie postępu"} loading="lazy" />
                              </button>
                            );
                          })}
                        </div>
                      )}
                      {audioAssets.length > 0 && (
                        <div className="client-history-entry__audio">
                          {audioAssets.map((asset) => (
                            <div className="client-audio-item" key={asset.id}>
                              <Icon name="mic" />
                              <div>
                                <strong>{asset.original_name || "Nagranie audio"}</strong>
                                <audio controls preload="none" src={withPin(asset.url)} onError={() => markAudioError(asset.id)} />
                                {audioErrors.includes(asset.id) && <small>Nie udało się załadować nagrania audio.</small>}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                      <div className="client-entry-comments">
                        {entry.comments.length > 0 && (
                          <div className="client-entry-comments__list">
                            {entry.comments.map((comment) => (
                              <article className={`client-entry-comment client-entry-comment--${comment.intent || "comment"}`} key={comment.id}>
                                <header>
                                  <strong>{commentAuthorLabel(comment)}</strong>
                                  {comment.intent && comment.intent !== "comment" && <em>{commentIntentLabel(comment)}</em>}
                                </header>
                                <small>{timeFormatter.format(new Date(comment.created_at))}</small>
                                <p>{comment.body}</p>
                              </article>
                            ))}
                          </div>
                        )}
                        <div className="client-entry-comment-form">
                          <textarea
                            value={commentDrafts[entry.id] || ""}
                            onChange={(event) => {
                              setCommentDrafts((current) => ({ ...current, [entry.id]: event.target.value }));
                              setCommentErrors((current) => ({ ...current, [entry.id]: "" }));
                              setCommentSuccess((current) => ({ ...current, [entry.id]: "" }));
                            }}
                            placeholder={entry.kind === "problem" ? "Dodaj komentarz albo notatkę do problemu..." : "Napisz komentarz do tego wpisu..."}
                            rows={3}
                          />
                          {entry.kind === "problem" && (
                            <div className="client-problem-actions">
                              <button type="button" disabled={commentBusy[entry.id]} onClick={() => void submitClientComment(entry, "confirm_resolved")}>Potwierdzam rozwiązanie</button>
                              <button type="button" disabled={commentBusy[entry.id]} onClick={() => void submitClientComment(entry, "still_open")}>Problem nadal wymaga poprawki</button>
                            </div>
                          )}
                          <div className="client-entry-comment-form__footer">
                            <span>{commentErrors[entry.id] || commentSuccess[entry.id]}</span>
                            <Button type="button" variant="secondary" busy={commentBusy[entry.id]} onClick={() => void submitClientComment(entry)}>Dodaj komentarz</Button>
                          </div>
                        </div>
                      </div>
                    </article>
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
                  <a className="client-report-link" href={withPin(report.pdf_url || `/api/public/projects/${token}/reports/${report.id}/pdf`)} target="_blank" rel="noreferrer" key={report.id}>
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
  const [projectsDirty, setProjectsDirty] = useState(false);
  const [section, setSection] = useState("home");
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [toast, setToast] = useState<Toast | null>(null);
  const [queueCount, setQueueCount] = useState(0);
  const [uiMode, setUiMode] = useUiMode(user);
  const toastTimer = useRef<number | null>(null);
  const offlineScopeKey = useMemo(() => userOfflineScope(user), [user]);

  const notify = useCallback((next: Toast) => {
    setToast(next);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 4000);
  }, []);

  const refreshQueue = useCallback(async () => {
    if (!offlineScopeKey) {
      setQueueCount(0);
      return;
    }
    setQueueCount(await queuedEntryCount(offlineScopeKey));
  }, [offlineScopeKey]);
  const loadProjects = useCallback(async () => {
    if (!user) return;
    const result = await api<Project[]>("/projects");
    const details = await Promise.all(result.map(async (project) => {
      try { return await api<Project>(`/projects/${project.id}`); } catch { return project; }
    }));
    setProjects(details);
    setProjectsDirty(false);
  }, [user]);

  const markProjectsDirty = useCallback(() => setProjectsDirty(true), []);

  const syncQueue = useCallback(async () => {
    if (!navigator.onLine || !offlineScopeKey) return;
    try {
      const syncedAny = await syncQueuedEntriesForScope(offlineScopeKey);
      if (syncedAny) markProjectsDirty();
    } finally {
      await refreshQueue();
    }
  }, [markProjectsDirty, offlineScopeKey, refreshQueue]);

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
    if (navigator.onLine) void syncQueue();
    const handleVisibility = () => {
      if (document.visibilityState === "visible") void syncQueue();
    };
    addEventListener("online", syncQueue);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      removeEventListener("online", syncQueue);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [refreshQueue, syncQueue]);

  useEffect(() => {
    api<User>("/me")
      .then(setUser)
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadProjects(); }, [loadProjects]);

  useEffect(() => {
    if (!selectedProject && projectsDirty) {
      void loadProjects();
    }
  }, [loadProjects, projectsDirty, selectedProject]);

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
      onProjectChanged={markProjectsDirty}
      offlineScopeKey={offlineScopeKey}
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
      offlineScopeKey={offlineScopeKey}
    />
  ) : visibleSection === "reports" ? (
    <ReportsPage user={user} projects={projects} onOpen={setSelectedProject} />
  ) : visibleSection === "portfolio" ? (
    <IndependentPortfolioPage user={user} projects={projects} onOpenSettings={() => setSection("settings")} />
  ) : visibleSection === "discover" && isInvestor(user) ? (
    <InvestorDiscoveryPage notify={notify} />
  ) : visibleSection === "postJob" && isInvestor(user) ? (
    <InvestorPostJobPage notify={notify} />
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
      {createOpen && <CreateProjectModal user={user} onClose={() => setCreateOpen(false)} onCreated={(project) => { setCreateOpen(false); setProjects((current) => [project, ...current]); markProjectsDirty(); setSelectedProject(project); notify({ kind: "success", message: "Zlecenie utworzone" }); }} />}
      {toast && <ToastView toast={toast} />}
    </>
  );
}

function GuestEntry({ token, notify, onQueue }: { token: string; notify: (toast: Toast) => void; onQueue: () => void }) {
  const [details, setDetails] = useState<{ project_id: string; project_name: string; label: string; kind: string; account_type: string; permission: string } | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState("");
  const offlineScopeKey = useMemo(() => guestOfflineScope(token), [token]);
  const syncGuestQueue = useCallback(async () => {
    if (!navigator.onLine || !offlineScopeKey) return;
    try {
      await syncQueuedEntriesForScope(offlineScopeKey);
    } finally {
      onQueue();
    }
  }, [offlineScopeKey, onQueue]);
  useEffect(() => {
    api<{ project_id: string; project_name: string; label: string; kind: string; account_type: string; permission: string }>(`/guest/${token}`)
      .then(setDetails)
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Link jest nieaktywny"));
  }, [token]);
  useEffect(() => {
    if (navigator.onLine) void syncGuestQueue();
    const handleVisibility = () => {
      if (document.visibilityState === "visible") void syncGuestQueue();
    };
    addEventListener("online", syncGuestQueue);
    document.addEventListener("visibilitychange", handleVisibility);
    return () => {
      removeEventListener("online", syncGuestQueue);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [syncGuestQueue]);
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
  return <><ProjectView projectId={details.project_id} guestToken={token} offlineScopeKey={offlineScopeKey} onBack={() => setAccepted(false)} notify={notify} onQueue={onQueue} /></>;
}

function ToastView({ toast }: { toast: Toast }) {
  return <div className={`toast toast--${toast.kind}`}><Icon name={toast.kind === "error" ? "alert" : toast.kind === "success" ? "check" : "sync"} /><span>{toast.message}</span></div>;
}

