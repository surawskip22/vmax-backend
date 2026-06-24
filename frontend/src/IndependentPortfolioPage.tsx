import { FormEvent, useEffect, useMemo, useState } from "react";
import { Icon } from "./icons";
import type { Project, User } from "./types";

type PortfolioStatus = "draft" | "published";

type PortfolioRealization = {
  id: string;
  projectId: string;
  title: string;
  description: string;
  dateStart: string;
  dateEnd: string;
  amount: string;
  showAmount: boolean;
  status: PortfolioStatus;
  coverTone: number;
  createdAt: string;
  updatedAt: string;
};

type PortfolioView = "dashboard" | "manage" | "preview";

type PortfolioDraft = {
  projectId: string;
  title: string;
  description: string;
  dateStart: string;
  dateEnd: string;
  amount: string;
  showAmount: boolean;
};

const storagePrefix = "panmajster_independent_portfolio_";
const portfolioTags = ["Remonty łazienek", "Wykończenia wnętrz", "Biały montaż", "Glazura i terakota", "Kuchnie na wymiar"];

function storageKey(user: User) {
  return `${storagePrefix}${user.id}`;
}

function safeSlug(value: string) {
  return value
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 42) || "samodzielnymajster";
}

function formatDate(value?: string | null) {
  if (!value) return "Nie ustawiono";
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("pl-PL", { day: "2-digit", month: "2-digit", year: "numeric" }).format(date);
}

function projectAmount(project: Project) {
  if (!project.contract_amount) return "";
  return `${project.contract_amount} ${project.contract_currency || "PLN"}`;
}

function projectLocation(project?: Project) {
  if (!project) return "Bez lokalizacji";
  return project.address || project.client_name || "Bez lokalizacji";
}

function dateRange(item: PortfolioRealization) {
  const start = formatDate(item.dateStart);
  const end = formatDate(item.dateEnd);
  if (start === "Nie ustawiono" && end === "Nie ustawiono") return "Nie ustawiono";
  if (start === end) return start;
  return `${start} - ${end}`;
}

function draftFromProject(project?: Project): PortfolioDraft {
  return {
    projectId: project?.id || "",
    title: project?.name || "",
    description: project?.portfolio_summary || project?.description || "Krótki opis realizacji widoczny na publicznej wizytówce.",
    dateStart: project?.planned_start_date || "",
    dateEnd: project?.planned_end_date || "",
    amount: project ? projectAmount(project) : "",
    showAmount: Boolean(project?.contract_amount),
  };
}

function realizationFromProject(project: Project, index: number): PortfolioRealization {
  const now = new Date().toISOString();
  return {
    id: `seed-${project.id}`,
    projectId: project.id,
    title: project.name,
    description: project.portfolio_summary || project.description || "Realizacja zakończona i gotowa do pokazania klientom.",
    dateStart: project.planned_start_date || "",
    dateEnd: project.planned_end_date || project.updated_at?.slice(0, 10) || "",
    amount: projectAmount(project),
    showAmount: Boolean(project.contract_amount),
    status: index < 3 ? "published" : "draft",
    coverTone: index,
    createdAt: now,
    updatedAt: now,
  };
}

function loadPortfolio(user: User): PortfolioRealization[] {
  try {
    const raw = localStorage.getItem(storageKey(user));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function savePortfolio(user: User, items: PortfolioRealization[]) {
  try {
    localStorage.setItem(storageKey(user), JSON.stringify(items));
  } catch {
    // Local persistence is best-effort; saving the project itself is not affected.
  }
}

function statusLabel(status: PortfolioStatus) {
  return status === "published" ? "Opublikowana" : "Szkic";
}

function PortfolioImage({ tone, large = false }: { tone: number; large?: boolean }) {
  return (
    <div className={`ic-portfolio-image ic-portfolio-image--${tone % 5} ${large ? "ic-portfolio-image--large" : ""}`}>
      <span />
    </div>
  );
}

function PortfolioModal({
  projects,
  editing,
  onClose,
  onSave,
}: {
  projects: Project[];
  editing: PortfolioRealization | null;
  onClose: () => void;
  onSave: (item: PortfolioRealization) => void;
}) {
  const initialProject = projects.find((project) => project.id === editing?.projectId) || projects[0];
  const [draft, setDraft] = useState<PortfolioDraft>(() => editing ? {
    projectId: editing.projectId,
    title: editing.title,
    description: editing.description,
    dateStart: editing.dateStart,
    dateEnd: editing.dateEnd,
    amount: editing.amount,
    showAmount: editing.showAmount,
  } : draftFromProject(initialProject));
  const selectedProject = projects.find((project) => project.id === draft.projectId);

  function updateProject(projectId: string) {
    const project = projects.find((item) => item.id === projectId);
    setDraft(draftFromProject(project));
  }

  function save(status: PortfolioStatus) {
    if (!draft.title.trim()) return;
    const now = new Date().toISOString();
    onSave({
      id: editing?.id || `realization-${Date.now()}`,
      projectId: draft.projectId,
      title: draft.title.trim(),
      description: draft.description.trim(),
      dateStart: draft.dateStart,
      dateEnd: draft.dateEnd,
      amount: draft.amount.trim(),
      showAmount: draft.showAmount,
      status,
      coverTone: editing?.coverTone ?? Math.max(0, projects.findIndex((project) => project.id === draft.projectId)),
      createdAt: editing?.createdAt || now,
      updatedAt: now,
    });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    save("published");
  }

  const galleryCount = Math.min(6, Math.max(4, selectedProject?.entry_count || 0));

  if (projects.length === 0) {
    return (
      <div className="modal-backdrop" role="presentation">
        <div className="modal ic-portfolio-modal" role="dialog" aria-modal="true" aria-labelledby="portfolio-empty-modal-title">
          <header className="modal__header">
            <div>
              <h2 id="portfolio-empty-modal-title">Dodaj realizację do wizytówki</h2>
              <p>Realizacja musi bazować na zakończonym zleceniu.</p>
            </div>
            <button type="button" className="icon-button" onClick={onClose} aria-label="Zamknij"><Icon name="close" /></button>
          </header>
          <div className="ic-portfolio-empty ic-portfolio-empty--modal">
            <Icon name="clipboard" />
            <strong>Brak zakończonych zleceń</strong>
            <p>Zakończ zlecenie, a potem dodaj je do publicznej wizytówki.</p>
            <button type="button" className="button button--secondary" onClick={onClose}>Zamknij</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal modal--wide ic-portfolio-modal" role="dialog" aria-modal="true" aria-labelledby="portfolio-modal-title">
        <header className="modal__header">
          <div>
            <h2 id="portfolio-modal-title">{editing ? "Edytuj realizację" : "Dodaj realizację do wizytówki"}</h2>
            <p>Jedno okno: wybierz zakończone zlecenie, uzupełnij opis i zdecyduj, czy publikujesz.</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Zamknij"><Icon name="close" /></button>
        </header>
        <form className="ic-portfolio-form" onSubmit={submit}>
          <section>
            <h3>1. Wybierz zakończone zlecenie</h3>
            <label>
              Zlecenie
              <select value={draft.projectId} onChange={(event) => updateProject(event.target.value)} disabled={Boolean(editing)}>
                {projects.map((project) => (
                  <option value={project.id} key={project.id}>{project.name} - {project.address || project.client_name || "bez adresu"}</option>
                ))}
              </select>
            </label>
          </section>

          <section>
            <h3>2. Informacje o realizacji</h3>
            <label>
              Tytuł realizacji
              <input value={draft.title} onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))} required />
            </label>
            <label>
              Opis publiczny
              <textarea rows={5} value={draft.description} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} required />
            </label>
            <div className="ic-portfolio-form__row">
              <label>
                Data od
                <input type="date" value={draft.dateStart} onChange={(event) => setDraft((current) => ({ ...current, dateStart: event.target.value }))} />
              </label>
              <label>
                Data do
                <input type="date" value={draft.dateEnd} onChange={(event) => setDraft((current) => ({ ...current, dateEnd: event.target.value }))} />
              </label>
              <label>
                Kwota realizacji
                <input value={draft.amount} onChange={(event) => setDraft((current) => ({ ...current, amount: event.target.value }))} placeholder="np. 12 500 zł" />
              </label>
            </div>
            <label className="ic-portfolio-toggle">
              <input type="checkbox" checked={draft.showAmount} onChange={(event) => setDraft((current) => ({ ...current, showAmount: event.target.checked }))} />
              Pokaż kwotę
            </label>
          </section>

          <section>
            <h3>3. Zdjęcia realizacji</h3>
            <div className="ic-portfolio-photo-picker">
              <div>
                <small>Zdjęcie główne</small>
                <PortfolioImage tone={editing?.coverTone ?? 0} large />
              </div>
              <div>
                <small>Galeria zdjęć z wpisów</small>
                <div className="ic-portfolio-gallery-picker">
                  {Array.from({ length: galleryCount }).map((_, index) => (
                    <PortfolioImage tone={index + 1} key={index} />
                  ))}
                  <button type="button" disabled><Icon name="plus" /> Dodaj zdjęcia</button>
                </div>
                <p>Na razie pokazujemy selekcję w UI. Trwałe podpięcie zdjęć portfolio zostaje w kolejnym kroku.</p>
              </div>
            </div>
          </section>

          <footer className="modal-actions ic-portfolio-form__actions">
            <button type="button" className="button button--secondary" onClick={() => save("draft")}>Zapisz szkic</button>
            <button type="submit" className="button button--primary"><Icon name="send" /> Opublikuj realizację</button>
          </footer>
        </form>
      </div>
    </div>
  );
}

export function IndependentPortfolioPage({
  user,
  projects,
  onOpenSettings,
}: {
  user: User;
  projects: Project[];
  onOpenSettings: () => void;
}) {
  const [view, setView] = useState<PortfolioView>("dashboard");
  const [items, setItems] = useState<PortfolioRealization[]>([]);
  const [editing, setEditing] = useState<PortfolioRealization | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [manageTab, setManageTab] = useState<"all" | PortfolioStatus>("all");
  const [copied, setCopied] = useState(false);
  const completedProjects = useMemo(() => projects.filter((project) => project.status === "completed"), [projects]);
  const projectsById = useMemo(() => new Map(projects.map((project) => [project.id, project])), [projects]);
  const profileSlug = safeSlug(user.name || user.email || "samodzielnymajster");
  const profileUrl = `panmajster.pl/wizytowka/${profileSlug}`;

  useEffect(() => {
    const saved = loadPortfolio(user);
    setItems(saved);
  }, [user.id]);

  useEffect(() => {
    if (items.length > 0 || completedProjects.length === 0) return;
    setItems(completedProjects.slice(0, 3).map(realizationFromProject));
  }, [completedProjects, items.length]);

  useEffect(() => {
    savePortfolio(user, items);
  }, [items, user]);

  const publishedItems = items.filter((item) => item.status === "published");
  const visibleItems = manageTab === "all" ? items : items.filter((item) => item.status === manageTab);
  const previewItem = publishedItems[0] || items[0] || null;
  const previewProject = previewItem ? projectsById.get(previewItem.projectId) : undefined;

  function upsertItem(item: PortfolioRealization) {
    setItems((current) => {
      const exists = current.some((candidate) => candidate.id === item.id);
      return exists
        ? current.map((candidate) => candidate.id === item.id ? item : candidate)
        : [item, ...current];
    });
    setModalOpen(false);
    setEditing(null);
  }

  function openCreate() {
    setEditing(null);
    setModalOpen(true);
  }

  function openEdit(item: PortfolioRealization) {
    setEditing(item);
    setModalOpen(true);
  }

  async function copyLink() {
    try {
      await navigator.clipboard?.writeText(`https://${profileUrl}`);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <div className="page ic-portfolio-page">
      <header className="ic-portfolio-header">
        <div>
          <span className="eyebrow">Moja wizytówka</span>
          <h1>{view === "manage" ? "Realizacje na mojej wizytówce" : view === "preview" ? "Podgląd publiczny" : "Moja wizytówka"}</h1>
          <p>
            {view === "manage"
              ? "Zarządzaj swoimi realizacjami widocznymi publicznie."
              : view === "preview"
                ? "Sprawdź, jak klient zobaczy wybraną realizację."
                : "Pokaż swoje najlepsze realizacje i zyskaj zaufanie klientów."}
          </p>
        </div>
        <div className="ic-portfolio-header__actions">
          {view !== "dashboard" && <button type="button" className="button button--secondary" onClick={() => setView("dashboard")}><Icon name="back" /> Wróć</button>}
          <button type="button" className="button button--secondary" onClick={() => setView("preview")}><Icon name="link" /> Podgląd publiczny</button>
          <button type="button" className="button button--primary" onClick={onOpenSettings}><Icon name="settings" /> Edytuj profil</button>
        </div>
      </header>

      {view === "manage" ? (
        <section className="ic-portfolio-manage panel">
          <header>
            <div>
              <h2>Realizacje na mojej wizytówce</h2>
              <p>{items.length === 0 ? "Nie masz jeszcze realizacji w wizytówce." : `1-${visibleItems.length} z ${items.length} realizacji`}</p>
            </div>
            <button type="button" className="button button--primary" onClick={openCreate}><Icon name="plus" /> Dodaj realizację</button>
          </header>
          <div className="ic-portfolio-manage-tabs" role="tablist" aria-label="Filtr realizacji">
            <button type="button" className={manageTab === "all" ? "active" : ""} onClick={() => setManageTab("all")}>Wszystkie <span>{items.length}</span></button>
            <button type="button" className={manageTab === "published" ? "active" : ""} onClick={() => setManageTab("published")}>Opublikowane <span>{publishedItems.length}</span></button>
            <button type="button" className={manageTab === "draft" ? "active" : ""} onClick={() => setManageTab("draft")}>Szkice <span>{items.length - publishedItems.length}</span></button>
          </div>
          <div className="ic-portfolio-manage-list">
            {visibleItems.length === 0 ? (
              <div className="ic-portfolio-empty"><Icon name="image" /><strong>Brak realizacji w tym widoku</strong><p>Dodaj realizację z zakończonego zlecenia albo zmień filtr.</p></div>
            ) : visibleItems.map((item) => {
              const project = projectsById.get(item.projectId);
              return (
                <article key={item.id}>
                  <PortfolioImage tone={item.coverTone} />
                  <div>
                    <strong>{item.title}</strong>
                    <small>{dateRange(item)} · {projectLocation(project)}</small>
                  </div>
                  <span className={`ic-portfolio-status ic-portfolio-status--${item.status}`}>{statusLabel(item.status)}</span>
                  <button type="button" className="icon-button" onClick={() => openEdit(item)} aria-label="Edytuj realizację"><Icon name="settings" /></button>
                  <button type="button" className="icon-button" aria-label="Więcej opcji"><Icon name="menu" /></button>
                </article>
              );
            })}
          </div>
        </section>
      ) : view === "preview" ? (
        <section className="ic-portfolio-public">
          <nav>
            <strong>{user.name || "Samodzielny Majster"}</strong>
            <span>O mnie</span>
            <span>Realizacje</span>
            <span>Kontakt</span>
          </nav>
          {!previewItem ? (
            <div className="ic-portfolio-empty"><Icon name="image" /><strong>Brak realizacji do podglądu</strong><p>Dodaj pierwszą realizację z zakończonego zlecenia.</p></div>
          ) : (
            <article className="ic-portfolio-public-card">
              <div>
                <PortfolioImage tone={previewItem.coverTone} large />
                <div className="ic-portfolio-public-thumbs">
                  {[0, 1, 2, 3].map((offset) => <PortfolioImage tone={previewItem.coverTone + offset} key={offset} />)}
                </div>
              </div>
              <div>
                <h2>{previewItem.title}</h2>
                <p className="ic-portfolio-location">{projectLocation(previewProject)}</p>
                <p>{previewItem.description}</p>
                <dl>
                  <div><dt>Data realizacji</dt><dd>{dateRange(previewItem)}</dd></div>
                  {previewItem.showAmount && previewItem.amount && <div><dt>Kwota realizacji</dt><dd>{previewItem.amount}</dd></div>}
                  <div><dt>Zakres prac</dt><dd>{portfolioTags.slice(0, 2).join(", ")}</dd></div>
                </dl>
                <button type="button" className="button button--primary"><Icon name="send" /> Skontaktuj się</button>
              </div>
            </article>
          )}
        </section>
      ) : (
        <>
          <section className="ic-portfolio-status-card panel">
            <div>
              <span><Icon name="link" /></span>
              <div><small>Status wizytówki</small><strong>{publishedItems.length > 0 ? "Opublikowana" : "Szkic"}</strong><p>Twoja wizytówka {publishedItems.length > 0 ? "jest widoczna publicznie." : "pojawi się publicznie po dodaniu realizacji."}</p></div>
            </div>
            <div>
              <small>Link do wizytówki</small>
              <strong>{profileUrl}</strong>
            </div>
            <button type="button" className="icon-button" onClick={copyLink} aria-label="Kopiuj link"><Icon name={copied ? "check" : "link"} /></button>
          </section>

          <div className="ic-portfolio-dashboard">
            <section className="ic-portfolio-realizations panel">
              <header>
                <div><h2>Moje realizacje</h2><p>{publishedItems.length} opublikowanych · {items.length - publishedItems.length} szkiców</p></div>
                <div>
                  <button type="button" className="button button--primary" onClick={openCreate}><Icon name="plus" /> Dodaj realizację</button>
                  <button type="button" className="text-button" onClick={() => setView("manage")}>Zarządzaj realizacjami →</button>
                </div>
              </header>
              {items.length === 0 ? (
                <div className="ic-portfolio-empty"><Icon name="image" /><strong>Dodaj pierwszą realizację</strong><p>Wybierz zakończone zlecenie i opisz efekt pracy.</p></div>
              ) : (
                <div className="ic-portfolio-grid">
                  {items.slice(0, 3).map((item) => {
                    const project = projectsById.get(item.projectId);
                    return (
                      <article key={item.id}>
                        <PortfolioImage tone={item.coverTone} large />
                        <div>
                          <strong>{item.title}</strong>
                          <span>{formatDate(item.dateEnd || item.updatedAt.slice(0, 10))} · {projectLocation(project)}</span>
                          <small>{project?.entry_count || 0} wpisy</small>
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </section>

            <aside className="ic-portfolio-profile panel">
              <button type="button" className="text-button" onClick={onOpenSettings}>Edytuj</button>
              <span className="ic-portfolio-profile__avatar"><Icon name="home" size={54} /></span>
              <h2>{user.name || "Samodzielny Majster"}</h2>
              <p>Specjalizuję się w remontach, wykończeniach wnętrz i pracach wykończeniowych na wysokim poziomie.</p>
              <div>{portfolioTags.map((tag) => <span key={tag}>{tag}</span>)}</div>
              <footer>
                <p><Icon name="send" /> {user.phone || "+48 123 456 789"}</p>
                <p><Icon name="link" /> {user.email}</p>
              </footer>
            </aside>
          </div>

          <section className="ic-portfolio-cta panel">
            <span><Icon name="phone" /></span>
            <div>
              <strong>Dodaj realizacje ze swoich zakończonych zleceń i buduj zaufanie klientów.</strong>
              <p>Pokaż jakość swojej pracy na profesjonalnej wizytówce.</p>
            </div>
          </section>
        </>
      )}

      {modalOpen && (
        <PortfolioModal
          projects={completedProjects}
          editing={editing}
          onClose={() => { setModalOpen(false); setEditing(null); }}
          onSave={upsertItem}
        />
      )}
    </div>
  );
}
