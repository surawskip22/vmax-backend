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
import type { Entry, Project, Report, User } from "./types";

type Toast = { kind: "success" | "error" | "info"; message: string };
type Route =
  | { kind: "marketing" }
  | { kind: "app" }
  | { kind: "guest"; token: string }
  | { kind: "report"; token: string }
  | { kind: "portfolio"; slug: string };

const statusLabels: Record<string, string> = {
  active: "W trakcie",
  paused: "Wstrzymane",
  completed: "Zakończone",
  archived: "Archiwum",
  planned: "Planowany",
};

function route(): Route {
  const matchGuest = location.pathname.match(/^\/g\/([^/]+)/);
  if (matchGuest) return { kind: "guest", token: matchGuest[1] };
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
}: {
  onClose: () => void;
  onSuccess: (user: User) => void;
}) {
  const [step, setStep] = useState<"email" | "code">("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
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

  return (
    <Modal title={step === "email" ? "Wejdź do Pan Majster" : "Sprawdź pocztę"} onClose={onClose}>
      {step === "email" ? (
        <form className="form-stack" onSubmit={requestCode}>
          <p className="form-intro">Bez hasła. Wyślemy Ci jednorazowy kod logowania.</p>
          <label>
            Adres e-mail
            <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoFocus />
          </label>
          {error && <p className="form-error">{error}</p>}
          <Button type="submit" busy={busy}>Wyślij kod</Button>
          <small>Logując się, akceptujesz warunki wersji testowej.</small>
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
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      const project = await api<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({
          name: data.get("name"),
          client_name: data.get("client_name"),
          client_email: data.get("client_email"),
          address: data.get("address"),
          description: data.get("description"),
          template: data.get("template"),
          workspace_id: data.get("workspace_id") || null,
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
    <Modal title="Nowe zlecenie" onClose={onClose}>
      <form className="form-stack" onSubmit={submit}>
        <label>Nazwa zlecenia<input name="name" placeholder="np. Remont łazienki" required autoFocus /></label>
        <div className="form-row">
          <label>Klient<input name="client_name" placeholder="Jan Kowalski" /></label>
          <label>E-mail klienta<input type="email" name="client_email" placeholder="klient@email.pl" /></label>
        </div>
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
        {user.workspaces.length > 0 && (
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
        <Button type="submit" busy={busy} icon="plus">Utwórz zlecenie</Button>
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
    ["team", "Zespół", "users"],
    ["settings", "Ustawienia", "settings"],
  ] as const;
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Logo />
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
  const active = projects.filter((project) => project.status === "active");
  const problems = projects.reduce((sum, item) => sum + (item.open_problem_count || 0), 0);
  return (
    <div className="page dashboard">
      <header className="page-header">
        <div>
          <span className="eyebrow">Środa, {new Intl.DateTimeFormat("pl", { day: "numeric", month: "long" }).format(new Date())}</span>
          <h1>Dzień dobry{user.name ? `, ${user.name.split(" ")[0]}` : ""}!</h1>
          <p>Tu masz szybki podgląd wszystkich realizacji.</p>
        </div>
        <Button icon="plus" onClick={onCreate}>Dodaj zlecenie</Button>
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
          <button className="text-button" onClick={onCreate}>+ Nowe zlecenie</button>
        </div>
        {projects.length === 0 ? (
          <EmptyState icon="clipboard" title="Dodaj pierwsze zlecenie" text="Projekt połączy zdjęcia, opisy, problemy i raporty w jedną historię.">
            <Button onClick={onCreate} icon="plus">Utwórz zlecenie</Button>
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
  projects,
  onProject,
  onCreate,
}: {
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
        <Button icon="plus" onClick={onCreate}>Dodaj zlecenie</Button>
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
  mode,
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
  const [stageId, setStageId] = useState(project.stages?.find((s) => s.status === "active")?.id || "");
  const [files, setFiles] = useState<File[]>([]);
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      chunks.current = [];
      mediaRecorder.ondataavailable = (event) => chunks.current.push(event.data);
      mediaRecorder.onstop = () => {
        const blob = new Blob(chunks.current, { type: mediaRecorder.mimeType || "audio/webm" });
        setFiles((current) => [...current, new File([blob], `opis-${Date.now()}.webm`, { type: blob.type })]);
        stream.getTracks().forEach((track) => track.stop());
      };
      recorder.current = mediaRecorder;
      mediaRecorder.start();
      setRecording(true);
    } catch {
      setError("Przeglądarka nie udostępniła mikrofonu. Możesz dodać tekst.");
    }
  }

  function stopRecording() {
    recorder.current?.stop();
    setRecording(false);
  }

  async function upload(entryId: string, selectedFiles: File[]) {
    for (const file of selectedFiles) {
      const data = new FormData();
      data.append("file", file);
      data.append("client_ref", crypto.randomUUID());
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
    <Modal title={kind === "problem" ? "Zgłoś problem" : mode === "photo" ? "Dodaj zdjęcia" : mode === "audio" ? "Nagraj opis" : "Nowy wpis"} onClose={onClose}>
      <form className="form-stack" onSubmit={submit}>
        {project.stages && project.stages.length > 0 && (
          <label>Etap<select value={stageId} onChange={(e) => setStageId(e.target.value)}><option value="">Bez etapu</option>{project.stages.map((stage) => <option value={stage.id} key={stage.id}>{stage.title}</option>)}</select></label>
        )}
        {(mode === "photo" || kind === "problem") && (
          <label className="upload-zone">
            <Icon name="camera" size={34} />
            <strong>Zrób lub wybierz zdjęcia</strong>
            <span>{files.filter((file) => file.type.startsWith("image/")).length ? `${files.filter((file) => file.type.startsWith("image/")).length} zdjęć wybranych` : "Możesz dodać kilka zdjęć naraz"}</span>
            <input type="file" accept="image/*" capture="environment" multiple onChange={(e) => setFiles((current) => [...current, ...Array.from(e.target.files || [])])} />
          </label>
        )}
        {(mode === "audio" || kind === "problem") && (
          <div className={`recorder ${recording ? "recorder--active" : ""}`}>
            <button type="button" onClick={recording ? stopRecording : startRecording}><Icon name="mic" size={30} /></button>
            <div><strong>{recording ? "Nagrywanie..." : "Opis głosowy"}</strong><span>{recording ? "Dotknij, aby zakończyć" : files.some((file) => file.type.startsWith("audio/")) ? "Nagranie jest gotowe" : "Dotknij mikrofonu i powiedz, co zrobiono"}</span></div>
          </div>
        )}
        <label>{kind === "problem" ? "Opis problemu" : "Opis prac"}<textarea rows={5} value={body} onChange={(e) => setBody(e.target.value)} placeholder={kind === "problem" ? "Co się wydarzyło i czego potrzeba?" : "Możesz wpisać opis lub poprawić go po transkrypcji..."} /></label>
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

function ManageProjectModal({
  project,
  onClose,
  onRefresh,
  notify,
}: {
  project: Project;
  onClose: () => void;
  onRefresh: () => void;
  notify: (toast: Toast) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [guestUrl, setGuestUrl] = useState("");
  const [tab, setTab] = useState<"details" | "stages" | "people" | "share">("details");

  async function saveDetails(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const data = new FormData(event.currentTarget);
    try {
      await api(`/projects/${project.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: data.get("name"),
          client_name: data.get("client_name"),
          client_email: data.get("client_email"),
          address: data.get("address"),
          description: data.get("description"),
          status: data.get("status"),
          portfolio_enabled: data.get("portfolio_enabled") === "on",
          portfolio_slug: data.get("portfolio_slug") || null,
          portfolio_summary: data.get("portfolio_summary"),
        }),
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

  async function addStage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    await api(`/projects/${project.id}/stages`, {
      method: "POST",
      body: JSON.stringify({ title: data.get("title") }),
    });
    form.reset();
    onRefresh();
  }

  async function invite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await api(`/projects/${project.id}/invite`, {
        method: "POST",
        body: JSON.stringify({ email: data.get("email"), role: data.get("role") }),
      });
      form.reset();
      notify({ kind: "success", message: "Zaproszenie zostało zapisane" });
      onRefresh();
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się zaprosić" });
    }
  }

  async function createGuest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const result = await api<{ url: string }>(`/projects/${project.id}/guest-links`, {
        method: "POST",
        body: JSON.stringify({
          label: data.get("label"),
          permission: data.get("permission"),
          expires_in_days: 30,
        }),
      });
      setGuestUrl(result.url);
      await navigator.clipboard?.writeText(result.url);
      notify({ kind: "success", message: "Link skopiowany. Wyślij go majstrowi przez SMS lub WhatsApp." });
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się utworzyć linku" });
    }
  }

  return (
    <Modal title="Zarządzaj zleceniem" onClose={onClose} wide>
      <div className="manage-tabs">
        <button className={tab === "details" ? "active" : ""} onClick={() => setTab("details")}>Dane</button>
        <button className={tab === "stages" ? "active" : ""} onClick={() => setTab("stages")}>Etapy</button>
        <button className={tab === "people" ? "active" : ""} onClick={() => setTab("people")}>Osoby</button>
        <button className={tab === "share" ? "active" : ""} onClick={() => setTab("share")}>Link dla majstra</button>
      </div>
      {tab === "details" && (
        <form className="form-stack" onSubmit={saveDetails}>
          <div className="form-row">
            <label>Nazwa<input name="name" defaultValue={project.name} required /></label>
            <label>Status<select name="status" defaultValue={project.status}><option value="active">W trakcie</option><option value="paused">Wstrzymane</option><option value="completed">Zakończone</option><option value="archived">Archiwum</option></select></label>
          </div>
          <div className="form-row">
            <label>Klient<input name="client_name" defaultValue={project.client_name} /></label>
            <label>E-mail klienta<input name="client_email" type="email" defaultValue={project.client_email} /></label>
          </div>
          <label>Adres<input name="address" defaultValue={project.address} /></label>
          <label>Opis<textarea name="description" rows={3} defaultValue={project.description} /></label>
          <div className="portfolio-settings">
            <label className="check-label"><input type="checkbox" name="portfolio_enabled" defaultChecked={project.portfolio_enabled} /> Pokaż realizację w publicznym portfolio</label>
            <label>Adres portfolio<input name="portfolio_slug" defaultValue={project.portfolio_slug || ""} placeholder="np. firma-kowalski" /></label>
            <label>Opis realizacji<textarea name="portfolio_summary" rows={3} defaultValue={project.portfolio_summary} /></label>
          </div>
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
          <form className="inline-form" onSubmit={addStage}><input name="title" placeholder="Nazwa nowego etapu" required /><Button type="submit" icon="plus">Dodaj etap</Button></form>
        </div>
      )}
      {tab === "people" && (
        <div className="manage-content">
          <div className="member-list">{project.members?.map((member) => <article key={member.id}><span>{(member.user.name || member.user.email).slice(0, 2).toUpperCase()}</span><div><strong>{member.user.name || member.user.email}</strong><small>{member.user.email}</small></div><b>{member.role}</b></article>)}</div>
          <form className="inline-form inline-form--three" onSubmit={invite}><input type="email" name="email" placeholder="E-mail współpracownika" required /><select name="role" defaultValue="contributor"><option value="viewer">Podgląd</option><option value="contributor">Dodawanie wpisów</option><option value="manager">Zarządzanie</option></select><Button type="submit">Zaproś</Button></form>
        </div>
      )}
      {tab === "share" && (
        <div className="manage-content">
          <div className="guest-explainer"><Icon name="link" size={34} /><div><h3>Szybki link bez zakładania konta</h3><p>Majster otworzy zlecenie na telefonie i od razu doda zdjęcia lub opis. Link możesz później odwołać.</p></div></div>
          <form className="form-stack form-stack--flat" onSubmit={createGuest}>
            <label>Podpis linku<input name="label" placeholder="np. Ekipa łazienka" required /></label>
            <label>Uprawnienia<select name="permission" defaultValue="history"><option value="add">Tylko dodawanie</option><option value="history">Dodawanie i historia</option><option value="view">Tylko podgląd</option></select></label>
            <Button type="submit" icon="link">Utwórz i skopiuj link</Button>
          </form>
          {guestUrl && <div className="share-result"><input value={guestUrl} readOnly /><Button variant="secondary" onClick={() => navigator.clipboard.writeText(guestUrl)}>Kopiuj</Button></div>}
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
  const [share, setShare] = useState<{ url: string; qr_url: string; pdf_url: string } | null>(null);
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
      const result = await api<{ url: string; qr_url: string; pdf_url: string }>(`/reports/${selected.id}/publish`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      setShare(result);
      await navigator.clipboard?.writeText(result.url);
      notify({ kind: "success", message: "Raport opublikowany. Link skopiowano." });
      onRefresh();
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się opublikować raportu" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title="Raport dla klienta" onClose={onClose} wide>
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
              {selected.pdf_url && <a className="button button--secondary" href={selected.pdf_url} target="_blank">Pobierz PDF</a>}
              {share && <><input readOnly value={share.url} /><Button variant="secondary" icon="link" onClick={() => navigator.clipboard.writeText(share.url)}>Kopiuj link</Button><img className="qr-preview" src={share.qr_url} alt="Kod QR raportu" /></>}
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
  onBack,
  notify,
  onQueue,
}: {
  projectId: string;
  guestToken?: string;
  onBack: () => void;
  notify: (toast: Toast) => void;
  onQueue: () => void;
}) {
  const [project, setProject] = useState<Project | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [reports, setReports] = useState<Report[]>([]);
  const [loading, setLoading] = useState(true);
  const [entryModal, setEntryModal] = useState<{ kind: "update" | "problem"; mode: "photo" | "audio" | "text" } | null>(null);
  const [showReports, setShowReports] = useState(false);
  const [showManage, setShowManage] = useState(false);
  const [fieldMode, setFieldMode] = useState(Boolean(guestToken) || innerWidth < 720);

  const load = useCallback(async () => {
    try {
      const [projectData, entryData] = await Promise.all([
        api<Project>(`/projects/${projectId}`, {}, guestToken),
        api<Entry[]>(`/projects/${projectId}/entries`, {}, guestToken),
      ]);
      setProject(projectData);
      setEntries(entryData);
      if (!guestToken) {
        setReports(await api<Report[]>(`/projects/${projectId}/reports`));
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

  if (fieldMode) {
    return (
      <div className="field-mode">
        <header className="field-mode__header">
          <button onClick={onBack}><Icon name="back" /></button>
          <div className="field-brand"><img src="/brand/app-icon.png" alt="" /><strong>Pan Majster</strong></div>
          <button onClick={() => setFieldMode(false)}><Icon name="menu" /></button>
        </header>
        <main>
          <section className="field-project">
            <small>ZLECENIE</small>
            <h1>{project.name}</h1>
            <p>{project.address}</p>
            <span className={`status status--${project.status}`}>● {statusLabels[project.status]}</span>
          </section>
          {!navigator.onLine && <div className="offline-banner"><Icon name="sync" /> Tryb offline — wpis zostanie wysłany po odzyskaniu sieci.</div>}
          {canAdd && <div className="field-actions">
            <FieldAction icon="camera" title="Zrób zdjęcie" subtitle="Postęp lub efekt" tone="navy" onClick={() => setEntryModal({ kind: "update", mode: "photo" })} />
            <FieldAction icon="mic" title="Nagraj opis" subtitle="Powiedz, co zrobiono" tone="orange" onClick={() => setEntryModal({ kind: "update", mode: "audio" })} />
            <FieldAction icon="alert" title="Zgłoś problem" subtitle="Usterka lub decyzja" tone="red" onClick={() => setEntryModal({ kind: "problem", mode: "photo" })} />
            {!guestToken && <FieldAction icon="send" title="Wyślij raport" subtitle="Link, QR i PDF" tone="navy" onClick={() => setShowReports(true)} />}
          </div>}
          <section className="field-latest">
            <div className="section-title"><h2>Ostatnie wpisy</h2><button onClick={() => setFieldMode(false)}>Pełna historia</button></div>
            {entries.length === 0 ? <EmptyState icon="camera" title="Jeszcze bez zdjęć" text="Dodaj pierwszy wpis z realizacji." /> : entries.slice(0, 3).map((entry) => <TimelineEntry item={entry} guestToken={guestToken} onRefresh={load} key={entry.id} />)}
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
            <Button variant="secondary" onClick={() => setFieldMode(true)}>Tryb budowy</Button>
            {!guestToken && ["owner", "manager"].includes(project.role || "") && <Button variant="secondary" icon="settings" onClick={() => setShowManage(true)}>Zarządzaj</Button>}
            {!guestToken && <Button icon="send" onClick={() => setShowReports(true)}>Utwórz raport</Button>}
          </div>
        </div>
      </header>
      <div className="project-layout">
        <aside className="project-summary panel">
          <h3>Postęp projektu</h3>
          <div className="progress-value"><strong>{progress}%</strong><span>{completed} z {project.stages?.length || 0} etapów</span></div>
          <div className="progress"><i style={{ width: `${progress}%` }} /></div>
          <div className="stage-list">{project.stages?.map((stage) => <div key={stage.id} className={`stage-item stage-item--${stage.status}`}><span>{stage.status === "completed" ? "✓" : stage.position + 1}</span><div><strong>{stage.title}</strong><small>{stage.status === "completed" ? "Zakończony" : stage.status === "active" ? "W trakcie" : "Zaplanowany"}</small></div></div>)}</div>
          {!guestToken && <div className="summary-meta"><div><small>Klient</small><strong>{project.client_name || "—"}</strong></div><div><small>Adres</small><strong>{project.address || "—"}</strong></div><div><small>Rola</small><strong>{project.role || "gość"}</strong></div></div>}
        </aside>
        <main className="project-timeline panel">
          <div className="panel__header">
            <div><h2>Oś czasu zlecenia</h2><p>Zdjęcia, opisy i zgłoszone problemy.</p></div>
            {canAdd && <div className="quick-buttons"><button onClick={() => setEntryModal({ kind: "update", mode: "photo" })}><Icon name="camera" /> Zdjęcia</button><button onClick={() => setEntryModal({ kind: "update", mode: "audio" })}><Icon name="mic" /> Głos</button><button className="problem" onClick={() => setEntryModal({ kind: "problem", mode: "photo" })}><Icon name="alert" /> Problem</button></div>}
          </div>
          {entries.length === 0 ? <EmptyState icon="camera" title="Tu powstanie historia pracy" text="Dodaj zdjęcia, nagranie głosowe albo pierwszy opis." /> : <div className="timeline">{entries.map((entry) => <TimelineEntry item={entry} guestToken={guestToken} onRefresh={load} key={entry.id} />)}</div>}
        </main>
      </div>
      {entryModal && <NewEntryModal project={project} kind={entryModal.kind} mode={entryModal.mode} guestToken={guestToken} onClose={() => setEntryModal(null)} onSaved={() => { setEntryModal(null); load(); notify({ kind: "success", message: "Wpis zapisany" }); }} onQueued={() => { setEntryModal(null); onQueue(); notify({ kind: "info", message: "Wpis zapisany offline" }); }} />}
      {showReports && <ReportModal project={project} reports={reports} onClose={() => setShowReports(false)} onRefresh={load} notify={notify} />}
      {showManage && <ManageProjectModal project={project} onClose={() => setShowManage(false)} onRefresh={load} notify={notify} />}
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

function TeamPage({ user, notify }: { user: User; notify: (toast: Toast) => void }) {
  const [showCreate, setShowCreate] = useState(false);
  async function createWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api("/workspaces", { method: "POST", body: JSON.stringify({ name: data.get("name"), kind: "company" }) });
      notify({ kind: "success", message: "Firma została utworzona. Odśwież aplikację, aby ją zobaczyć." });
      setShowCreate(false);
    } catch (reason) {
      notify({ kind: "error", message: reason instanceof Error ? reason.message : "Nie udało się utworzyć firmy" });
    }
  }
  return (
    <div className="page">
      <header className="page-header"><div><span className="eyebrow">Organizacje</span><h1>Zespół</h1><p>Firma może mieć własnych pracowników, ale role nadal nadajesz per projekt.</p></div><Button icon="plus" onClick={() => setShowCreate(true)}>Dodaj firmę</Button></header>
      <section className="panel">
        {user.workspaces.length === 0 ? <EmptyState icon="users" title="Pracujesz samodzielnie" text="Możesz tworzyć projekty bez firmy albo utworzyć zespół i zapraszać stałych współpracowników."><Button onClick={() => setShowCreate(true)}>Utwórz firmę</Button></EmptyState> : <div className="workspace-grid">{user.workspaces.map((workspace) => <article key={workspace.id}><span><Icon name="users" /></span><h3>{workspace.name}</h3><p>Twoja rola: {workspace.role}</p></article>)}</div>}
      </section>
      {showCreate && <Modal title="Nowa firma" onClose={() => setShowCreate(false)}><form className="form-stack" onSubmit={createWorkspace}><label>Nazwa firmy<input name="name" required autoFocus /></label><Button type="submit">Utwórz firmę</Button></form></Modal>}
    </div>
  );
}

function SettingsPage({ user, onUpdated, notify }: { user: User; onUpdated: (user: User) => void; notify: (toast: Toast) => void }) {
  const [admin, setAdmin] = useState<any>(null);
  useEffect(() => {
    if (user.is_admin) api("/admin/overview").then(setAdmin).catch(() => null);
  }, [user.is_admin]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const updated = await api<User>("/me", { method: "PATCH", body: JSON.stringify({ name: data.get("name"), phone: data.get("phone"), locale: "pl" }) });
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
          <div className="beta-box"><Icon name="check" /><div><strong>Dostęp testowy aktywny</strong><p>Twoje konto ma bezpłatny dostęp do pilotażu Pan Majster.</p></div></div>
          <Button type="submit">Zapisz profil</Button>
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

function PublicReport({ token }: { token: string }) {
  const [report, setReport] = useState<any>(null);
  const [requiresPin, setRequiresPin] = useState(false);
  const [pin, setPin] = useState("");
  const [error, setError] = useState("");
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
        {item.content.stages?.map((stage, index) => <section className="public-report__stage" key={stage.title}><span className="stage-number">{index + 1}</span><div><h2>{stage.title}</h2>{stage.entries.map((entry) => <article key={entry.entry_id}><small>{entry.date}</small><p>{entry.text}</p>{entry.media_ids && entry.media_ids.length > 0 && <div className="media-grid">{entry.media_ids.map((id) => <img src={`/api/public/reports/${token}/media/${id}${pin ? `?pin=${encodeURIComponent(pin)}` : ""}`} alt="" key={id} />)}</div>}</article>)}</div></section>)}
      </main>
      <footer>Raport wygenerowany w aplikacji Pan Majster · Zdjęcie. Głos. Raport.</footer>
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

  async function logout() {
    await api("/auth/logout", { method: "POST" });
    setUser(null);
    setProjects([]);
    navigate("/");
  }

  if (currentRoute.kind === "report") return <PublicReport token={currentRoute.token} />;
  if (currentRoute.kind === "portfolio") return <PublicPortfolio slug={currentRoute.slug} />;
  if (currentRoute.kind === "guest") {
    return <GuestEntry token={currentRoute.token} notify={notify} onQueue={refreshQueue} />;
  }

  const marketing = currentRoute.kind === "marketing" && !user;
  if (marketing) {
    return <><Marketing onLogin={() => setAuthOpen(true)} />{authOpen && <AuthModal onClose={() => setAuthOpen(false)} onSuccess={(next) => { setUser(next); setAuthOpen(false); navigate("/app"); }} />}{toast && <ToastView toast={toast} />}</>;
  }
  if (loading || !user) {
    return <><div className="splash"><img src="/brand/app-icon.png" alt="Pan Majster" /><span className="spinner" /></div>{authOpen && <AuthModal onClose={() => { setAuthOpen(false); navigate("/"); }} onSuccess={(next) => { setUser(next); setAuthOpen(false); navigate("/app"); }} />}</>;
  }

  const body = selectedProject ? (
    <ProjectView projectId={selectedProject.id} onBack={() => setSelectedProject(null)} notify={notify} onQueue={refreshQueue} />
  ) : section === "projects" ? (
    <ProjectsPage projects={projects} onProject={setSelectedProject} onCreate={() => setCreateOpen(true)} />
  ) : section === "reports" ? (
    <ReportsPage projects={projects} onOpen={setSelectedProject} />
  ) : section === "team" ? (
    <TeamPage user={user} notify={notify} />
  ) : section === "settings" ? (
    <SettingsPage user={user} onUpdated={setUser} notify={notify} />
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
  const [projectId, setProjectId] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    api<{ project_id: string }>(`/guest/${token}`)
      .then((data) => setProjectId(data.project_id))
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Link jest nieaktywny"));
  }, [token]);
  if (error) return <div className="public-page"><Logo /><EmptyState icon="alert" title="Link jest nieaktywny" text={error} /></div>;
  if (!projectId) return <div className="splash"><img src="/brand/app-icon.png" alt="Pan Majster" /><span className="spinner" /></div>;
  return <><ProjectView projectId={projectId} guestToken={token} onBack={() => navigate("/")} notify={notify} onQueue={onQueue} /></>;
}

function ToastView({ toast }: { toast: Toast }) {
  return <div className={`toast toast--${toast.kind}`}><Icon name={toast.kind === "error" ? "alert" : toast.kind === "success" ? "check" : "sync"} /><span>{toast.message}</span></div>;
}
