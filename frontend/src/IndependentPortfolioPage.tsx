import { FormEvent, useEffect, useMemo, useState } from "react";
import { Icon } from "./icons";
import type { Project, User } from "./types";

type PortfolioStatus = "draft" | "published";
type ReviewSource = "manual" | "verified_client_link";

type PortfolioRealization = {
  id: string;
  projectId: string;
  title: string;
  description: string;
  locationPublic: string;
  workScope: string[];
  dateStart: string;
  dateEnd: string;
  amount: string;
  showAmount: boolean;
  status: PortfolioStatus;
  coverTone: number;
  coverUrl?: string;
  galleryUrls?: string[];
  createdAt: string;
  updatedAt: string;
};

type PortfolioReview = {
  id: string;
  clientName: string;
  date: string;
  rating: number;
  text: string;
  body: string;
  published: boolean;
  source: ReviewSource;
  sourceLabel: string;
  projectId?: string;
  realizationId?: string;
  projectTitle?: string;
  realizationTitle?: string;
  createdAt: string;
  updatedAt: string;
};

type PortfolioView = "dashboard" | "manage" | "preview";

type PortfolioDraft = {
  projectId: string;
  title: string;
  description: string;
  locationPublic: string;
  workScope: string[];
  dateStart: string;
  dateEnd: string;
  amount: string;
  showAmount: boolean;
  coverUrl?: string;
  galleryUrls: string[];
};

type ReviewDraft = {
  clientName: string;
  date: string;
  rating: number;
  body: string;
  published: boolean;
  realizationId: string;
};

const portfolioAvatarOptions = [
  { icon: "home", label: "Dom" },
  { icon: "wrench", label: "Hydraulika" },
  { icon: "hammer", label: "Remonty" },
  { icon: "paint", label: "Malowanie" },
  { icon: "brush", label: "Wykończenia" },
  { icon: "pipe", label: "Instalacje" },
  { icon: "electric", label: "Elektryka" },
  { icon: "tile", label: "Glazura" },
  { icon: "kitchen", label: "Kuchnie" },
  { icon: "leaf", label: "Ogród" },
  { icon: "broom", label: "Sprzątanie" },
  { icon: "laptop", label: "IT" },
  { icon: "car", label: "Mechanika" },
  { icon: "camera", label: "Foto" },
  { icon: "tools", label: "Usługi" },
] as const;

type PortfolioAvatarIcon = (typeof portfolioAvatarOptions)[number]["icon"];

type PortfolioProfile = {
  displayName: string;
  publicDescription: string;
  serviceArea: string;
  publicPhone: string;
  publicEmail: string;
  whatsapp: string;
  tags: string[];
  avatarIcon: PortfolioAvatarIcon;
  updatedAt: string;
};

type PortfolioProfileDraft = {
  displayName: string;
  publicDescription: string;
  serviceArea: string;
  publicPhone: string;
  publicEmail: string;
  whatsapp: string;
  tags: string[];
  avatarIcon: PortfolioAvatarIcon;
};

type DeleteTarget =
  | { kind: "realization"; id: string; title: string }
  | { kind: "review"; id: string; title: string };

const portfolioStoragePrefix = "panmajster_independent_portfolio_";
const reviewStoragePrefix = "panmajster_independent_portfolio_reviews_";
const profileStoragePrefix = "panmajster_independent_portfolio_profile_";
const defaultPortfolioTags = ["Remonty łazienek", "Wykończenia wnętrz", "Biały montaż", "Glazura i terakota", "Kuchnie na wymiar"];
const defaultWorkScopeTags = ["Demontaż", "Hydraulika", "Glazura", "Biały montaż", "Malowanie", "Zabudowa GK"];
const galleryLimit = 10;

function storageIdentity(user: User) {
  return user.id || user.email || "anonymous";
}

function portfolioStorageKey(user: User) {
  return `${portfolioStoragePrefix}${storageIdentity(user)}`;
}

function reviewsStorageKey(user: User) {
  return `${reviewStoragePrefix}${storageIdentity(user)}`;
}

function profileStorageKey(user: User) {
  return `${profileStoragePrefix}${storageIdentity(user)}`;
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

function todayIso() {
  return new Date().toISOString().slice(0, 10);
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

function publicLocationFromProject(project?: Project) {
  if (!project) return "Warszawa i okolice";
  const source = project.address || project.client_name || "";
  const parts = source.split(",").map((part) => part.trim()).filter(Boolean);
  return parts.length > 1 ? parts[parts.length - 1] : parts[0] || "Warszawa i okolice";
}

function projectLocation(project?: Project) {
  if (!project) return "Bez lokalizacji";
  return project.address || project.client_name || "Bez lokalizacji";
}

function realizationLocation(item: PortfolioRealization, project?: Project) {
  return item.locationPublic || publicLocationFromProject(project);
}

function realizationScope(item: PortfolioRealization, profile?: PortfolioProfile) {
  const tags = Array.isArray(item.workScope) ? item.workScope.filter(Boolean) : [];
  if (tags.length > 0) return tags;
  return profile?.tags?.slice(0, 3) || defaultWorkScopeTags.slice(0, 3);
}

function projectImageUrls(project?: Project) {
  const entries = (project as Project & { entries?: Array<{ media?: Array<{ kind?: string; url?: string }> }> } | undefined)?.entries || [];
  return entries
    .flatMap((entry) => entry.media || [])
    .filter((media) => media.kind === "image" && media.url)
    .map((media) => media.url as string)
    .slice(0, galleryLimit);
}

function normalizeRealization(value: unknown, index: number): PortfolioRealization | null {
  if (!value || typeof value !== "object") return null;
  const data = value as Partial<PortfolioRealization> & { tags?: string[]; location?: string };
  const now = new Date().toISOString();
  const title = data.title?.trim();
  if (!title) return null;
  const galleryUrls = Array.isArray(data.galleryUrls) ? data.galleryUrls.filter(Boolean).slice(0, galleryLimit) : [];
  return {
    id: data.id || `realization-${Date.now()}-${index}`,
    projectId: data.projectId || "",
    title,
    description: data.description || "Realizacja zakończona i gotowa do pokazania klientom.",
    locationPublic: data.locationPublic || data.location || "",
    workScope: Array.isArray(data.workScope) && data.workScope.length > 0
      ? data.workScope.filter(Boolean).slice(0, 10)
      : Array.isArray(data.tags) ? data.tags.filter(Boolean).slice(0, 10) : [],
    dateStart: data.dateStart || "",
    dateEnd: data.dateEnd || "",
    amount: data.amount || "",
    showAmount: Boolean(data.showAmount),
    status: data.status === "published" ? "published" : "draft",
    coverTone: typeof data.coverTone === "number" && Number.isFinite(data.coverTone) ? data.coverTone : index,
    coverUrl: data.coverUrl || galleryUrls[0],
    galleryUrls,
    createdAt: data.createdAt || now,
    updatedAt: data.updatedAt || data.createdAt || now,
  };
}

function reviewSourceLabel(source: ReviewSource) {
  return source === "verified_client_link" ? "Zweryfikowana przez klienta" : "Dodana ręcznie";
}

function normalizeReview(value: unknown, index: number): PortfolioReview | null {
  if (!value || typeof value !== "object") return null;
  const data = value as Partial<PortfolioReview> & { visible?: boolean };
  const clientName = data.clientName?.trim();
  const body = (data.body || data.text || "").trim();
  if (!clientName || !body) return null;
  const now = new Date().toISOString();
  const source: ReviewSource = data.source === "verified_client_link" ? "verified_client_link" : "manual";
  return {
    id: data.id || `review-${Date.now()}-${index}`,
    clientName,
    date: data.date || todayIso(),
    rating: Math.max(1, Math.min(5, Number(data.rating) || 5)),
    text: body,
    body,
    published: data.published ?? data.visible ?? true,
    source,
    sourceLabel: data.sourceLabel || reviewSourceLabel(source),
    projectId: data.projectId,
    realizationId: data.realizationId,
    projectTitle: data.projectTitle,
    realizationTitle: data.realizationTitle,
    createdAt: data.createdAt || now,
    updatedAt: data.updatedAt || data.createdAt || now,
  };
}

function defaultPortfolioProfile(user: User): PortfolioProfile {
  return {
    displayName: user.name || "Samodzielny Majster",
    publicDescription: "Specjalizuję się w remontach, wykończeniach wnętrz i pracach wykończeniowych na wysokim poziomie.",
    serviceArea: "Warszawa i okolice",
    publicPhone: user.phone || "+48 123 456 789",
    publicEmail: user.email || "",
    whatsapp: user.phone || "",
    tags: defaultPortfolioTags,
    avatarIcon: "home",
    updatedAt: new Date().toISOString(),
  };
}

function normalizeProfile(value: unknown, user: User): PortfolioProfile {
  const defaults = defaultPortfolioProfile(user);
  if (!value || typeof value !== "object") return defaults;
  const data = value as Partial<PortfolioProfile>;
  const iconExists = portfolioAvatarOptions.some((option) => option.icon === data.avatarIcon);
  return {
    ...defaults,
    ...data,
    displayName: data.displayName || defaults.displayName,
    publicDescription: data.publicDescription || defaults.publicDescription,
    publicPhone: data.publicPhone ?? defaults.publicPhone,
    publicEmail: data.publicEmail ?? defaults.publicEmail,
    whatsapp: data.whatsapp ?? defaults.whatsapp,
    tags: Array.isArray(data.tags) && data.tags.length > 0 ? data.tags.filter(Boolean).slice(0, 12) : defaults.tags,
    avatarIcon: iconExists ? data.avatarIcon as PortfolioAvatarIcon : defaults.avatarIcon,
    updatedAt: data.updatedAt || defaults.updatedAt,
  };
}

function loadProfile(user: User): PortfolioProfile {
  try {
    const raw = localStorage.getItem(profileStorageKey(user));
    return raw ? normalizeProfile(JSON.parse(raw), user) : defaultPortfolioProfile(user);
  } catch {
    return defaultPortfolioProfile(user);
  }
}

function saveProfile(user: User, profile: PortfolioProfile) {
  try {
    localStorage.setItem(profileStorageKey(user), JSON.stringify(profile));
  } catch {
    // Public profile persistence is best-effort local MVP storage.
  }
}

function profileDraft(profile: PortfolioProfile): PortfolioProfileDraft {
  return {
    displayName: profile.displayName,
    publicDescription: profile.publicDescription,
    serviceArea: profile.serviceArea,
    publicPhone: profile.publicPhone,
    publicEmail: profile.publicEmail,
    whatsapp: profile.whatsapp,
    tags: profile.tags,
    avatarIcon: profile.avatarIcon,
  };
}

function cleanPhone(value: string) {
  return value.trim().replace(/[^\d+]/g, "");
}

function phoneHref(value: string) {
  const phone = cleanPhone(value);
  return phone ? `tel:${phone}` : "";
}

function mailHref(value: string) {
  const email = value.trim();
  return email ? `mailto:${email}` : "";
}

function whatsappHref(value: string) {
  const phone = value.trim().replace(/\D/g, "");
  return phone ? `https://wa.me/${phone}` : "";
}

function scrollToPublicSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function dateRange(item: PortfolioRealization) {
  const start = formatDate(item.dateStart);
  const end = formatDate(item.dateEnd);
  if (start === "Nie ustawiono" && end === "Nie ustawiono") return "Nie ustawiono";
  if (start === end) return start;
  return `${start} - ${end}`;
}

function draftFromProject(project?: Project): PortfolioDraft {
  const availableImages = projectImageUrls(project);
  return {
    projectId: project?.id || "",
    title: project?.name || "",
    description: project?.portfolio_summary || project?.description || "Krótki opis realizacji widoczny na publicznej wizytówce.",
    locationPublic: publicLocationFromProject(project),
    workScope: defaultWorkScopeTags.slice(0, 3),
    dateStart: project?.planned_start_date || "",
    dateEnd: project?.planned_end_date || "",
    amount: project ? projectAmount(project) : "",
    showAmount: Boolean(project?.contract_amount),
    coverUrl: availableImages[0],
    galleryUrls: availableImages,
  };
}

function realizationFromProject(project: Project, index: number): PortfolioRealization {
  const now = new Date().toISOString();
  return {
    id: `seed-${project.id}`,
    projectId: project.id,
    title: project.name,
    description: project.portfolio_summary || project.description || "Realizacja zakończona i gotowa do pokazania klientom.",
    locationPublic: publicLocationFromProject(project),
    workScope: defaultWorkScopeTags.slice(0, 3),
    dateStart: project.planned_start_date || "",
    dateEnd: project.planned_end_date || project.updated_at?.slice(0, 10) || "",
    amount: projectAmount(project),
    showAmount: Boolean(project.contract_amount),
    status: index < 3 ? "published" : "draft",
    coverTone: index,
    galleryUrls: [],
    createdAt: now,
    updatedAt: now,
  };
}

function loadPortfolio(user: User): PortfolioRealization[] {
  try {
    const raw = localStorage.getItem(portfolioStorageKey(user));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(normalizeRealization).filter((item): item is PortfolioRealization => Boolean(item)) : [];
  } catch {
    return [];
  }
}

function savePortfolio(user: User, items: PortfolioRealization[]) {
  try {
    localStorage.setItem(portfolioStorageKey(user), JSON.stringify(items));
  } catch {
    // Local persistence is best-effort; saving the project itself is not affected.
  }
}

function loadReviews(user: User): PortfolioReview[] {
  try {
    const raw = localStorage.getItem(reviewsStorageKey(user));
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.map(normalizeReview).filter((review): review is PortfolioReview => Boolean(review)) : [];
  } catch {
    return [];
  }
}

function saveReviews(user: User, reviews: PortfolioReview[]) {
  try {
    localStorage.setItem(reviewsStorageKey(user), JSON.stringify(reviews));
  } catch {
    // Reviews are local MVP data; failing to persist does not affect the app.
  }
}

function statusLabel(status: PortfolioStatus) {
  return status === "published" ? "Opublikowana" : "Szkic";
}

function reviewDraft(review?: PortfolioReview | null): ReviewDraft {
  return {
    clientName: review?.clientName || "",
    date: review?.date || todayIso(),
    rating: review?.rating || 5,
    body: review?.body || "",
    published: review?.published ?? true,
    realizationId: review?.realizationId || "",
  };
}

function averageRating(reviews: PortfolioReview[]) {
  const visible = reviews.filter((review) => review.published);
  if (visible.length === 0) return null;
  const score = visible.reduce((sum, review) => sum + review.rating, 0) / visible.length;
  return score.toFixed(1);
}

function stars(rating: number) {
  return "★★★★★".slice(0, Math.max(1, Math.min(5, rating)));
}

function PortfolioImage({
  tone,
  src,
  large = false,
}: {
  tone: number;
  src?: string;
  large?: boolean;
}) {
  return (
    <div className={`ic-portfolio-image ic-portfolio-image--${tone % 5} ${large ? "ic-portfolio-image--large" : ""}`}>
      {src ? <img src={src} alt="" loading="lazy" /> : <span />}
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
    locationPublic: editing.locationPublic,
    workScope: realizationScope(editing),
    dateStart: editing.dateStart,
    dateEnd: editing.dateEnd,
    amount: editing.amount,
    showAmount: editing.showAmount,
    coverUrl: editing.coverUrl,
    galleryUrls: editing.galleryUrls || [],
  } : draftFromProject(initialProject));
  const [scopeInput, setScopeInput] = useState("");
  const selectedProject = projects.find((project) => project.id === draft.projectId);
  const availableImages = projectImageUrls(selectedProject);

  function updateProject(projectId: string) {
    const project = projects.find((item) => item.id === projectId);
    setDraft(draftFromProject(project));
  }

  function addScopeTag(tag: string) {
    const clean = tag.trim();
    if (!clean) return;
    setDraft((current) => current.workScope.includes(clean) || current.workScope.length >= 10
      ? current
      : { ...current, workScope: [...current.workScope, clean] });
    setScopeInput("");
  }

  function removeScopeTag(tag: string) {
    setDraft((current) => ({ ...current, workScope: current.workScope.filter((item) => item !== tag) }));
  }

  function setCover(url: string) {
    setDraft((current) => ({
      ...current,
      coverUrl: url,
      galleryUrls: current.galleryUrls.includes(url) ? current.galleryUrls : [url, ...current.galleryUrls].slice(0, galleryLimit),
    }));
  }

  function toggleGallery(url: string) {
    setDraft((current) => {
      const exists = current.galleryUrls.includes(url);
      if (exists) {
        const nextGallery = current.galleryUrls.filter((item) => item !== url);
        return { ...current, galleryUrls: nextGallery, coverUrl: current.coverUrl === url ? nextGallery[0] : current.coverUrl };
      }
      if (current.galleryUrls.length >= galleryLimit) return current;
      return { ...current, galleryUrls: [...current.galleryUrls, url], coverUrl: current.coverUrl || url };
    });
  }

  function save(status: PortfolioStatus) {
    if (!draft.title.trim()) return;
    const now = new Date().toISOString();
    onSave({
      id: editing?.id || `realization-${Date.now()}`,
      projectId: draft.projectId,
      title: draft.title.trim(),
      description: draft.description.trim(),
      locationPublic: draft.locationPublic.trim(),
      workScope: draft.workScope.map((tag) => tag.trim()).filter(Boolean).slice(0, 10),
      dateStart: draft.dateStart,
      dateEnd: draft.dateEnd,
      amount: draft.amount.trim(),
      showAmount: draft.showAmount,
      status,
      coverTone: editing?.coverTone ?? Math.max(0, projects.findIndex((project) => project.id === draft.projectId)),
      coverUrl: draft.coverUrl,
      galleryUrls: draft.galleryUrls,
      createdAt: editing?.createdAt || now,
      updatedAt: now,
    });
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    save("published");
  }

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
            <h3>Źródło realizacji</h3>
            <label>
              Wybierz zakończone zlecenie
              <select value={draft.projectId} onChange={(event) => updateProject(event.target.value)} disabled={Boolean(editing)}>
                {projects.map((project) => (
                  <option value={project.id} key={project.id}>{project.name} - {project.address || project.client_name || "bez adresu"}</option>
                ))}
              </select>
            </label>
            <p className="form-note">Dane robocze pomagają wypełnić realizację, ale publicznie pokazujemy tylko wybrane informacje.</p>
          </section>

          <section>
            <h3>Opis publiczny</h3>
            <label>
              Tytuł realizacji
              <input value={draft.title} onChange={(event) => setDraft((current) => ({ ...current, title: event.target.value }))} required />
            </label>
            <label>
              Opis publiczny
              <textarea rows={5} value={draft.description} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} required />
            </label>
            <label>
              Lokalizacja publiczna
              <input value={draft.locationPublic} onChange={(event) => setDraft((current) => ({ ...current, locationPublic: event.target.value }))} placeholder="np. Mokotów, Warszawa, Piaseczno" />
            </label>
            <p className="form-note">To opis widoczny na wizytówce. Nie musi być taki sam jak roboczy opis zlecenia.</p>
          </section>

          <section>
            <h3>Zakres prac</h3>
            <div className="ic-scope-editor">
              <input value={scopeInput} onChange={(event) => setScopeInput(event.target.value)} placeholder="Dodaj zakres prac, np. Hydraulika" />
              <button type="button" className="button button--secondary" onClick={() => addScopeTag(scopeInput)}>Dodaj</button>
            </div>
            <div className="ic-scope-presets" aria-label="Podpowiedzi zakresu prac">
              {defaultWorkScopeTags.map((tag) => (
                <button type="button" key={tag} onClick={() => addScopeTag(tag)} disabled={draft.workScope.includes(tag) || draft.workScope.length >= 10}>{tag}</button>
              ))}
            </div>
            <div className="ic-profile-tags ic-realization-tags" aria-label="Zakres prac realizacji">
              {draft.workScope.length === 0 ? <span>Dodaj tagi zakresu wykonanej pracy.</span> : draft.workScope.map((tag) => (
                <button type="button" key={tag} onClick={() => removeScopeTag(tag)}>
                  {tag}
                  <Icon name="close" size={14} />
                </button>
              ))}
            </div>
          </section>

          <section>
            <h3>Daty i kwota</h3>
            <div className="ic-portfolio-form__row">
              <label>
                Data rozpoczęcia
                <input type="date" value={draft.dateStart} onChange={(event) => setDraft((current) => ({ ...current, dateStart: event.target.value }))} />
              </label>
              <label>
                Data zakończenia
                <input type="date" value={draft.dateEnd} onChange={(event) => setDraft((current) => ({ ...current, dateEnd: event.target.value }))} />
              </label>
              <label>
                Kwota realizacji
                <input value={draft.amount} onChange={(event) => setDraft((current) => ({ ...current, amount: event.target.value }))} placeholder="np. 12 500 zł" />
              </label>
            </div>
            <label className="ic-portfolio-toggle">
              <input type="checkbox" checked={draft.showAmount} onChange={(event) => setDraft((current) => ({ ...current, showAmount: event.target.checked }))} />
              Pokaż kwotę na wizytówce
            </label>
            <p className="form-note">Kwota jest opcjonalna. Możesz ją ukryć w publicznej wizytówce.</p>
          </section>

          <section>
            <h3>Zdjęcie główne</h3>
            <div className="ic-portfolio-photo-picker">
              <div className="ic-portfolio-cover-preview">
                <small>Aktualne zdjęcie główne</small>
                <PortfolioImage tone={editing?.coverTone ?? 0} src={draft.coverUrl} large />
              </div>
              <div>
                <small>Wybierz z dostępnych zdjęć</small>
                {availableImages.length === 0 ? (
                  <p className="form-note">Brak zdjęć do wyboru dla tej realizacji. Zdjęcia z zakończonego zlecenia będą dostępne po podpięciu galerii do backendu.</p>
                ) : (
                  <div className="ic-portfolio-gallery-picker">
                    {availableImages.map((src, index) => (
                      <button type="button" className={draft.coverUrl === src ? "active" : ""} onClick={() => setCover(src)} key={src}>
                        <PortfolioImage tone={index + 1} src={src} />
                        {draft.coverUrl === src && <span>Zdjęcie główne</span>}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </section>

          <section>
            <h3>Galeria zdjęć</h3>
            {availableImages.length === 0 ? (
              <p className="form-note">Brak zdjęć do wyboru dla tej realizacji. Placeholder będzie widoczny tylko tam, gdzie brakuje zdjęć.</p>
            ) : (
              <>
                <p className="form-note">Wybrano {draft.galleryUrls.length} z {availableImages.length} zdjęć. Limit galerii: {galleryLimit} zdjęć.</p>
                <div className="ic-portfolio-gallery-picker ic-portfolio-gallery-picker--selectable">
                  {availableImages.map((src, index) => {
                    const checked = draft.galleryUrls.includes(src);
                    return (
                      <button type="button" className={checked ? "active" : ""} onClick={() => toggleGallery(src)} key={src}>
                        <PortfolioImage tone={index + 2} src={src} />
                        {checked && <span><Icon name="check" size={14} /> Wybrane</span>}
                      </button>
                    );
                  })}
                </div>
              </>
            )}
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

function ReviewModal({
  editing,
  realizations,
  onClose,
  onSave,
}: {
  editing: PortfolioReview | null;
  realizations: PortfolioRealization[];
  onClose: () => void;
  onSave: (review: PortfolioReview) => void;
}) {
  const [draft, setDraft] = useState<ReviewDraft>(() => reviewDraft(editing));
  const selectedRealization = realizations.find((item) => item.id === draft.realizationId);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft.clientName.trim() || !draft.body.trim()) return;
    const now = new Date().toISOString();
    const source: ReviewSource = editing?.source === "verified_client_link" ? "verified_client_link" : "manual";
    onSave({
      id: editing?.id || `review-${Date.now()}`,
      clientName: draft.clientName.trim(),
      date: draft.date,
      rating: Math.max(1, Math.min(5, Number(draft.rating) || 5)),
      text: draft.body.trim(),
      body: draft.body.trim(),
      published: draft.published,
      source,
      sourceLabel: reviewSourceLabel(source),
      projectId: selectedRealization?.projectId,
      realizationId: selectedRealization?.id,
      projectTitle: selectedRealization?.title,
      realizationTitle: selectedRealization?.title,
      createdAt: editing?.createdAt || now,
      updatedAt: now,
    });
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal ic-portfolio-modal" role="dialog" aria-modal="true" aria-labelledby="portfolio-review-modal-title">
        <header className="modal__header">
          <div>
            <h2 id="portfolio-review-modal-title">{editing ? "Edytuj opinię" : "Dodaj opinię klienta"}</h2>
            <p>To ręczna opinia MVP. Zweryfikowane opinie od klientów będą dodane później przez link klienta.</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Zamknij"><Icon name="close" /></button>
        </header>
        <form className="ic-portfolio-form" onSubmit={submit}>
          <section>
            <label>
              Imię klienta
              <input value={draft.clientName} onChange={(event) => setDraft((current) => ({ ...current, clientName: event.target.value }))} placeholder="np. Anna K." required />
            </label>
            <div className="ic-portfolio-form__row">
              <label>
                Ocena
                <select value={draft.rating} onChange={(event) => setDraft((current) => ({ ...current, rating: Number(event.target.value) }))}>
                  {[5, 4, 3, 2, 1].map((rating) => <option value={rating} key={rating}>{rating} gwiazdek</option>)}
                </select>
              </label>
              <label>
                Data
                <input type="date" value={draft.date} onChange={(event) => setDraft((current) => ({ ...current, date: event.target.value }))} />
              </label>
              <label className="ic-portfolio-toggle">
                <input type="checkbox" checked={draft.published} onChange={(event) => setDraft((current) => ({ ...current, published: event.target.checked }))} />
                Pokaż na wizytówce
              </label>
            </div>
            {realizations.length > 0 && (
              <label>
                Powiąż z realizacją
                <select value={draft.realizationId} onChange={(event) => setDraft((current) => ({ ...current, realizationId: event.target.value }))}>
                  <option value="">Brak powiązania</option>
                  {realizations.map((item) => <option value={item.id} key={item.id}>{item.title}</option>)}
                </select>
              </label>
            )}
            <label>
              Treść opinii
              <textarea value={draft.body} onChange={(event) => setDraft((current) => ({ ...current, body: event.target.value }))} placeholder="Krótka opinia klienta o realizacji..." required />
            </label>
            <p className="form-note">Ręcznej opinii nie oznaczamy jako zweryfikowanej. Weryfikacja przez klienta pojawi się w osobnym kroku z linkiem do zakończonego zlecenia.</p>
          </section>
          <footer className="modal-actions ic-portfolio-form__actions">
            <button type="button" className="button button--secondary" onClick={onClose}>Anuluj</button>
            <button type="submit" className="button button--primary">Zapisz opinię</button>
          </footer>
        </form>
      </div>
    </div>
  );
}

function ProfileModal({
  profile,
  onClose,
  onSave,
}: {
  profile: PortfolioProfile;
  onClose: () => void;
  onSave: (profile: PortfolioProfile) => void;
}) {
  const [draft, setDraft] = useState<PortfolioProfileDraft>(() => profileDraft(profile));
  const [tagInput, setTagInput] = useState("");

  function addTag() {
    const value = tagInput.trim();
    if (!value) return;
    setDraft((current) => {
      if (current.tags.some((tag) => tag.toLowerCase() === value.toLowerCase())) return current;
      return { ...current, tags: [...current.tags, value].slice(0, 12) };
    });
    setTagInput("");
  }

  function removeTag(tagToRemove: string) {
    setDraft((current) => ({ ...current, tags: current.tags.filter((tag) => tag !== tagToRemove) }));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const displayName = draft.displayName.trim() || profile.displayName;
    onSave({
      displayName,
      publicDescription: draft.publicDescription.trim() || profile.publicDescription,
      serviceArea: draft.serviceArea.trim(),
      publicPhone: draft.publicPhone.trim(),
      publicEmail: draft.publicEmail.trim(),
      whatsapp: draft.whatsapp.trim(),
      tags: draft.tags.map((tag) => tag.trim()).filter(Boolean).slice(0, 12),
      avatarIcon: draft.avatarIcon,
      updatedAt: new Date().toISOString(),
    });
  }

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal modal--wide ic-portfolio-modal ic-profile-modal" role="dialog" aria-modal="true" aria-labelledby="portfolio-profile-modal-title">
        <header className="modal__header">
          <div>
            <h2 id="portfolio-profile-modal-title">Edytuj dane wyświetlane</h2>
            <p>Te dane są widoczne na Twojej publicznej wizytówce. Nie zmieniają loginu ani danych konta.</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Zamknij"><Icon name="close" /></button>
        </header>
        <form className="ic-portfolio-form ic-profile-form" onSubmit={submit}>
          <section>
            <h3>Dane publiczne</h3>
            <div className="ic-portfolio-form__row ic-profile-form__row">
              <label>
                Nazwa wyświetlana
                <input value={draft.displayName} onChange={(event) => setDraft((current) => ({ ...current, displayName: event.target.value }))} required />
              </label>
              <label>
                Obszar działania
                <input value={draft.serviceArea} onChange={(event) => setDraft((current) => ({ ...current, serviceArea: event.target.value }))} placeholder="np. Warszawa i okolice" />
              </label>
            </div>
            <label>
              Opis publiczny
              <textarea rows={5} value={draft.publicDescription} onChange={(event) => setDraft((current) => ({ ...current, publicDescription: event.target.value }))} />
            </label>
          </section>

          <section>
            <h3>Kontakt publiczny</h3>
            <div className="ic-portfolio-form__row">
              <label>
                Telefon publiczny
                <input value={draft.publicPhone} onChange={(event) => setDraft((current) => ({ ...current, publicPhone: event.target.value }))} placeholder="+48 123 456 789" />
              </label>
              <label>
                E-mail publiczny
                <input type="email" value={draft.publicEmail} onChange={(event) => setDraft((current) => ({ ...current, publicEmail: event.target.value }))} placeholder="kontakt@example.com" />
              </label>
              <label>
                Numer WhatsApp
                <input value={draft.whatsapp} onChange={(event) => setDraft((current) => ({ ...current, whatsapp: event.target.value }))} placeholder="+48 123 456 789" />
              </label>
            </div>
          </section>

          <section>
            <h3>Tagi / usługi</h3>
            <div className="ic-profile-tag-editor">
              <input value={tagInput} onChange={(event) => setTagInput(event.target.value)} placeholder="np. Hydraulika" />
              <button type="button" className="button button--secondary" onClick={addTag}>Dodaj</button>
            </div>
            <div className="ic-profile-tags" aria-label="Wybrane tagi profilu">
              {draft.tags.length === 0 ? <span>Brak tagów. Dodaj usługi, które chcesz pokazać klientom.</span> : draft.tags.map((tag) => (
                <button type="button" key={tag} onClick={() => removeTag(tag)}>
                  {tag}
                  <Icon name="close" size={14} />
                </button>
              ))}
            </div>
          </section>

          <section>
            <h3>Ikona / avatar profilu</h3>
            <div className="ic-avatar-grid">
              {portfolioAvatarOptions.map((option) => (
                <button
                  type="button"
                  key={option.icon}
                  className={`ic-avatar-choice ${draft.avatarIcon === option.icon ? "active" : ""}`}
                  onClick={() => setDraft((current) => ({ ...current, avatarIcon: option.icon }))}
                >
                  <Icon name={option.icon} />
                  <span>{option.label}</span>
                </button>
              ))}
            </div>
          </section>

          <footer className="modal-actions ic-portfolio-form__actions">
            <button type="button" className="button button--secondary" onClick={onClose}>Anuluj</button>
            <button type="submit" className="button button--primary">Zapisz dane wyświetlane</button>
          </footer>
        </form>
      </div>
    </div>
  );
}

function ContactModal({
  profile,
  onClose,
}: {
  profile: PortfolioProfile;
  onClose: () => void;
}) {
  const whatsappNumber = profile.whatsapp || profile.publicPhone;
  const contacts = [
    { label: "Zadzwoń", detail: profile.publicPhone, href: phoneHref(profile.publicPhone), icon: "phone" },
    { label: "Wyślij e-mail", detail: profile.publicEmail, href: mailHref(profile.publicEmail), icon: "link" },
    { label: "Napisz na WhatsApp", detail: whatsappNumber, href: whatsappHref(whatsappNumber), icon: "send" },
  ].filter((item) => item.href);

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal ic-portfolio-modal ic-contact-modal" role="dialog" aria-modal="true" aria-labelledby="portfolio-contact-modal-title">
        <header className="modal__header">
          <div>
            <h2 id="portfolio-contact-modal-title">Skontaktuj się</h2>
            <p>Wybierz wygodny sposób kontaktu z wykonawcą.</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Zamknij"><Icon name="close" /></button>
        </header>
        <div className="ic-contact-options">
          {contacts.length === 0 ? (
            <div className="ic-portfolio-empty ic-portfolio-empty--compact">
              <Icon name="phone" />
              <strong>Brak publicznych danych kontaktowych</strong>
              <p>Ten wykonawca nie udostępnił jeszcze telefonu, e-maila ani WhatsApp.</p>
            </div>
          ) : contacts.map((contact) => (
            <a className="ic-contact-option" href={contact.href} target={contact.href.startsWith("http") ? "_blank" : undefined} rel={contact.href.startsWith("http") ? "noreferrer" : undefined} key={contact.label}>
              <Icon name={contact.icon} />
              <span>{contact.label}</span>
              <small>{contact.detail}</small>
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}

function RealizationPreviewModal({
  item,
  project,
  profile,
  onClose,
  onContact,
}: {
  item: PortfolioRealization;
  project?: Project;
  profile: PortfolioProfile;
  onClose: () => void;
  onContact: () => void;
}) {
  const tags = realizationScope(item, profile);
  const gallery = item.galleryUrls?.length ? item.galleryUrls : item.coverUrl ? [item.coverUrl] : [];
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal modal--wide ic-portfolio-modal ic-realization-preview-modal" role="dialog" aria-modal="true" aria-labelledby="realization-preview-title">
        <header className="modal__header">
          <div>
            <h2 id="realization-preview-title">{item.title}</h2>
            <p>{realizationLocation(item, project)} · {dateRange(item)}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="Zamknij"><Icon name="close" /></button>
        </header>
        <div className="ic-realization-preview">
          <div>
            <PortfolioImage tone={item.coverTone} src={item.coverUrl} large />
            {gallery.length > 0 && (
              <div className="ic-portfolio-public-thumbs">
                {gallery.slice(0, 6).map((src, index) => <PortfolioImage tone={item.coverTone + index + 1} src={src} key={`${item.id}-preview-${index}`} />)}
              </div>
            )}
          </div>
          <section>
            <span className={`ic-portfolio-status ic-portfolio-status--${item.status}`}>{statusLabel(item.status)}</span>
            <h3>{item.title}</h3>
            <p>{item.description}</p>
            <dl>
              <div><dt>Lokalizacja</dt><dd>{realizationLocation(item, project)}</dd></div>
              <div><dt>Data realizacji</dt><dd>{dateRange(item)}</dd></div>
              {item.showAmount && item.amount && <div><dt>Kwota realizacji</dt><dd>{item.amount}</dd></div>}
            </dl>
            <div className="ic-public-tags">{tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
            <button type="button" className="button button--primary" onClick={onContact}><Icon name="send" /> Skontaktuj się</button>
          </section>
        </div>
      </div>
    </div>
  );
}

function ConfirmDeleteModal({
  target,
  onCancel,
  onConfirm,
}: {
  target: DeleteTarget;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const isRealization = target.kind === "realization";
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal ic-portfolio-confirm" role="dialog" aria-modal="true" aria-labelledby="portfolio-delete-title">
        <header className="modal__header">
          <div>
            <h2 id="portfolio-delete-title">{isRealization ? "Usunąć realizację z wizytówki?" : "Usunąć opinię?"}</h2>
            <p>{target.title}</p>
          </div>
          <button type="button" className="icon-button" onClick={onCancel} aria-label="Zamknij"><Icon name="close" /></button>
        </header>
        <div className="ic-portfolio-confirm__body">
          <p>
            {isRealization
              ? "Realizacja zniknie z Twojej publicznej wizytówki. Zlecenie, wpisy i zdjęcia robocze nie zostaną usunięte."
              : "Opinia zostanie usunięta tylko z lokalnej wizytówki. Dane zleceń i wpisów pozostaną bez zmian."}
          </p>
        </div>
        <footer className="modal-actions">
          <button type="button" className="button button--secondary" onClick={onCancel}>Anuluj</button>
          <button type="button" className="button button--danger" onClick={onConfirm}>{isRealization ? "Usuń z wizytówki" : "Usuń opinię"}</button>
        </footer>
      </div>
    </div>
  );
}

function ReviewsSection({
  reviews,
  publicOnly = false,
  onAdd,
  onEdit,
  onToggle,
  onDelete,
}: {
  reviews: PortfolioReview[];
  publicOnly?: boolean;
  onAdd?: () => void;
  onEdit?: (review: PortfolioReview) => void;
  onToggle?: (review: PortfolioReview) => void;
  onDelete?: (review: PortfolioReview) => void;
}) {
  const visibleReviews = publicOnly ? reviews.filter((review) => review.published) : reviews;
  const displayedReviews = visibleReviews;
  const rating = averageRating(reviews);

  return (
    <section className={`ic-portfolio-reviews panel ${publicOnly ? "ic-portfolio-reviews--public" : ""}`}>
      <header>
        <div>
          <span className="ic-portfolio-review-icon"><Icon name="check" /></span>
          <div>
            <h2>Opinie klientów</h2>
            <p>
              {rating
                ? `Średnia ocena ${rating}/5 z ${reviews.filter((review) => review.published).length} widocznych opinii.`
                : publicOnly
                  ? "Ten wykonawca nie ma jeszcze publicznych opinii."
                  : "Na razie opinie możesz dodać ręcznie. W przyszłości klient wystawi opinię przez link po zakończonym zleceniu."}
            </p>
          </div>
        </div>
        {!publicOnly && <button type="button" className="button button--secondary" onClick={onAdd}>Dodaj opinię</button>}
      </header>

      {!publicOnly && (
        <div className="ic-review-future-card">
          <span><Icon name="link" /></span>
          <div>
            <strong>Zweryfikowane opinie klientów</strong>
            <p>Później klient będzie mógł wystawić opinię tylko przez link do zakończonego zlecenia. Dzięki temu opinie będą powiązane z realną realizacją.</p>
          </div>
          <em>Dostępne później</em>
        </div>
      )}

      {displayedReviews.length === 0 ? (
        <div className="ic-portfolio-empty ic-portfolio-empty--compact">
          <Icon name="report" />
          <strong>{publicOnly ? "Brak publicznych opinii" : "Brak opinii klientów"}</strong>
          <p>
            {publicOnly
              ? "Ten wykonawca nie ma jeszcze publicznych opinii."
              : "Opinie pojawią się tutaj, gdy dodasz je ręcznie lub gdy w przyszłości klient wystawi opinię po zakończonym zleceniu."}
          </p>
          {!publicOnly && <button type="button" className="button button--primary" onClick={onAdd}>Dodaj opinię ręcznie</button>}
        </div>
      ) : (
        <div className="ic-portfolio-review-list">
          {displayedReviews.map((review) => (
            <article key={review.id}>
              <div>
                <div className="ic-review-title-row">
                  <strong>{review.clientName}</strong>
                  {!publicOnly && <span className="ic-review-source">{review.sourceLabel || reviewSourceLabel(review.source)}</span>}
                  {publicOnly && review.source === "verified_client_link" && <span className="ic-review-source ic-review-source--verified">{reviewSourceLabel(review.source)}</span>}
                </div>
                <span>{stars(review.rating)} · {formatDate(review.date)}</span>
                {(review.realizationTitle || review.projectTitle) && <small>Realizacja: {review.realizationTitle || review.projectTitle}</small>}
                <p>{review.body}</p>
              </div>
              {!publicOnly && (
                <div className="ic-portfolio-review-actions">
                  <span className={`ic-portfolio-status ic-portfolio-status--${review.published ? "published" : "draft"}`}>{review.published ? "Widoczna" : "Ukryta"}</span>
                  <button type="button" className="text-button" onClick={() => onEdit?.(review)}>Edytuj</button>
                  <button type="button" className="text-button" onClick={() => onToggle?.(review)}>{review.published ? "Ukryj" : "Pokaż"}</button>
                  <button type="button" className="text-button text-button--danger" onClick={() => onDelete?.(review)}>Usuń</button>
                </div>
              )}
            </article>
          ))}
        </div>
      )}
    </section>
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
  const [reviews, setReviews] = useState<PortfolioReview[]>([]);
  const [portfolioLoadedFor, setPortfolioLoadedFor] = useState("");
  const [reviewsLoadedFor, setReviewsLoadedFor] = useState("");
  const [editing, setEditing] = useState<PortfolioRealization | null>(null);
  const [editingReview, setEditingReview] = useState<PortfolioReview | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [reviewModalOpen, setReviewModalOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [manageTab, setManageTab] = useState<"all" | PortfolioStatus>("all");
  const [copied, setCopied] = useState(false);
  const [profile, setProfile] = useState<PortfolioProfile>(() => defaultPortfolioProfile(user));
  const [profileLoadedFor, setProfileLoadedFor] = useState("");
  const [profileModalOpen, setProfileModalOpen] = useState(false);
  const [contactModalOpen, setContactModalOpen] = useState(false);
  const [previewRealization, setPreviewRealization] = useState<PortfolioRealization | null>(null);
  const userStorageId = storageIdentity(user);
  const completedProjects = useMemo(() => projects.filter((project) => project.status === "completed"), [projects]);
  const projectsById = useMemo(() => new Map(projects.map((project) => [project.id, project])), [projects]);
  const profileSlug = safeSlug(profile.displayName || user.name || user.email || "samodzielnymajster");
  const profileUrl = `panmajster.pl/wizytowka/${profileSlug}`;
  void onOpenSettings;

  useEffect(() => {
    const identity = storageIdentity(user);
    setItems(loadPortfolio(user));
    setReviews(loadReviews(user));
    setProfile(loadProfile(user));
    setPortfolioLoadedFor(identity);
    setReviewsLoadedFor(identity);
    setProfileLoadedFor(identity);
  }, [user.id, user.email]);

  useEffect(() => {
    if (portfolioLoadedFor !== userStorageId || items.length > 0 || completedProjects.length === 0) return;
    setItems(completedProjects.slice(0, 3).map(realizationFromProject));
  }, [completedProjects, items.length, portfolioLoadedFor, userStorageId]);

  useEffect(() => {
    if (portfolioLoadedFor !== userStorageId) return;
    savePortfolio(user, items);
  }, [items, portfolioLoadedFor, user, userStorageId]);

  useEffect(() => {
    if (reviewsLoadedFor !== userStorageId) return;
    saveReviews(user, reviews);
  }, [reviews, reviewsLoadedFor, user, userStorageId]);

  useEffect(() => {
    if (profileLoadedFor !== userStorageId) return;
    saveProfile(user, profile);
  }, [profile, profileLoadedFor, user, userStorageId]);

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

  function upsertReview(review: PortfolioReview) {
    setReviews((current) => {
      const exists = current.some((candidate) => candidate.id === review.id);
      return exists
        ? current.map((candidate) => candidate.id === review.id ? review : candidate)
        : [review, ...current];
    });
    setReviewModalOpen(false);
    setEditingReview(null);
  }

  function openCreate() {
    setEditing(null);
    setModalOpen(true);
  }

  function openEdit(item: PortfolioRealization) {
    setEditing(item);
    setModalOpen(true);
  }

  function openReview(review: PortfolioReview | null) {
    setEditingReview(review);
    setReviewModalOpen(true);
  }

  function toggleItemStatus(item: PortfolioRealization) {
    upsertItem({ ...item, status: item.status === "published" ? "draft" : "published", updatedAt: new Date().toISOString() });
  }

  function toggleReviewStatus(review: PortfolioReview) {
    upsertReview({ ...review, published: !review.published, updatedAt: new Date().toISOString() });
  }

  function deleteSelectedTarget() {
    if (!deleteTarget) return;
    if (deleteTarget.kind === "realization") {
      setItems((current) => current.filter((item) => item.id !== deleteTarget.id));
    } else {
      setReviews((current) => current.filter((review) => review.id !== deleteTarget.id));
    }
    setDeleteTarget(null);
  }

  async function copyLink(item?: PortfolioRealization) {
    try {
      const suffix = item ? `#${item.id}` : "";
      await navigator.clipboard?.writeText(`https://${profileUrl}${suffix}`);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  if (view === "preview") {
    return (
      <>
        <div className="page ic-portfolio-page ic-portfolio-page--public-preview">
          <div className="ic-portfolio-preview-bar">
            <strong>Podgląd publiczny — tak klient zobaczy Twoją wizytówkę</strong>
            <button type="button" className="button button--secondary" onClick={() => setView("dashboard")}><Icon name="back" /> Wróć do edycji</button>
          </div>
          <section className="ic-portfolio-public">
            <nav>
              <strong>{profile.displayName}</strong>
              <button type="button" onClick={() => scrollToPublicSection("portfolio-about")}>O mnie</button>
              <button type="button" onClick={() => scrollToPublicSection("portfolio-realizations")}>Realizacje</button>
              <button type="button" onClick={() => scrollToPublicSection("portfolio-reviews")}>Opinie</button>
              <button type="button" onClick={() => scrollToPublicSection("portfolio-contact")}>Kontakt</button>
            </nav>
            <section className="ic-portfolio-public-profile" id="portfolio-about">
              <span className="ic-portfolio-profile__avatar"><Icon name={profile.avatarIcon} size={54} /></span>
              <div>
                <small>Profil wykonawcy</small>
                <h1>{profile.displayName}</h1>
                <p>{profile.publicDescription}</p>
                <div>{profile.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
              </div>
              <button type="button" className="button button--primary" onClick={() => setContactModalOpen(true)}><Icon name="send" /> Skontaktuj się</button>
            </section>
            {!previewItem ? (
              <div className="ic-portfolio-empty"><Icon name="image" /><strong>Brak realizacji do podglądu</strong><p>Ten wykonawca nie opublikował jeszcze realizacji.</p></div>
            ) : (
              <article className="ic-portfolio-public-card">
                <div>
                  <PortfolioImage tone={previewItem.coverTone} src={previewItem.coverUrl} large />
                  <div className="ic-portfolio-public-thumbs">
                    {(previewItem.galleryUrls?.length ? previewItem.galleryUrls : [undefined, undefined, undefined, undefined]).slice(0, 4).map((src, index) => (
                      <PortfolioImage tone={previewItem.coverTone + index} src={src} key={`${previewItem.id}-${index}`} />
                    ))}
                  </div>
                </div>
                <div>
                  <h2>{previewItem.title}</h2>
                  <p className="ic-portfolio-location">{realizationLocation(previewItem, previewProject)}</p>
                  <p>{previewItem.description}</p>
                  <div className="ic-public-tags">{realizationScope(previewItem, profile).map((tag) => <span key={tag}>{tag}</span>)}</div>
                  <dl>
                    <div><dt>Data realizacji</dt><dd>{dateRange(previewItem)}</dd></div>
                    {previewItem.showAmount && previewItem.amount && <div><dt>Kwota realizacji</dt><dd>{previewItem.amount}</dd></div>}
                    <div><dt>Zdjęcia</dt><dd>{previewItem.galleryUrls?.length || 0} zdjęć</dd></div>
                  </dl>
                  <button type="button" className="button button--secondary" onClick={() => setPreviewRealization(previewItem)}>Podgląd realizacji</button>
                </div>
              </article>
            )}
            <section className="ic-portfolio-public-section" id="portfolio-realizations">
              <h2>Realizacje</h2>
              {publishedItems.length === 0 ? (
                <p className="form-note">Ten wykonawca nie opublikował jeszcze realizacji.</p>
              ) : (
                <div className="ic-portfolio-public-realization-grid">
                  {publishedItems.slice(0, 6).map((item) => {
                    const project = projectsById.get(item.projectId);
                    return (
                      <article key={item.id}>
                        <PortfolioImage tone={item.coverTone} src={item.coverUrl} />
                        <div>
                          <strong>{item.title}</strong>
                          <small>{dateRange(item)} · {realizationLocation(item, project)}</small>
                          <p>{item.description}</p>
                          <div className="ic-public-tags">{realizationScope(item, profile).slice(0, 4).map((tag) => <span key={tag}>{tag}</span>)}</div>
                          {item.showAmount && item.amount && <em>{item.amount}</em>}
                        </div>
                        <button type="button" className="text-button" onClick={() => setPreviewRealization(item)}>Podgląd</button>
                      </article>
                    );
                  })}
                </div>
              )}
            </section>
            <div id="portfolio-reviews">
              <ReviewsSection reviews={reviews} publicOnly />
            </div>
            <section className="ic-portfolio-public-contact" id="portfolio-contact">
              <h2>Kontakt</h2>
              <p><Icon name="phone" /> {profile.publicPhone || "Telefon nieudostępniony"}</p>
              <p><Icon name="link" /> {profile.publicEmail || "E-mail nieudostępniony"}</p>
              <p><Icon name="send" /> {profile.serviceArea || "Obszar działania nieuzupełniony"}</p>
              <button type="button" className="button button--primary" onClick={() => setContactModalOpen(true)}><Icon name="send" /> Skontaktuj się</button>
            </section>
          </section>
        </div>
        {previewRealization && (
          <RealizationPreviewModal
            item={previewRealization}
            project={projectsById.get(previewRealization.projectId)}
            profile={profile}
            onClose={() => setPreviewRealization(null)}
            onContact={() => {
              setPreviewRealization(null);
              setContactModalOpen(true);
            }}
          />
        )}
        {contactModalOpen && <ContactModal profile={profile} onClose={() => setContactModalOpen(false)} />}
      </>
    );
  }

  return (
    <div className="page ic-portfolio-page">
      <header className="ic-portfolio-header">
        <div>
          <span className="eyebrow">Moja wizytówka</span>
          <h1>{view === "manage" ? "Realizacje na mojej wizytówce" : "Moja wizytówka"}</h1>
          <p>{view === "manage" ? "Zarządzaj swoimi realizacjami widocznymi publicznie." : "Pokaż swoje najlepsze realizacje i zyskaj zaufanie klientów."}</p>
        </div>
        <div className="ic-portfolio-header__actions">
          {view !== "dashboard" && <button type="button" className="button button--secondary" onClick={() => setView("dashboard")}><Icon name="back" /> Wróć</button>}
          <button type="button" className="button button--secondary" onClick={() => setView("preview")}><Icon name="link" /> Podgląd publiczny</button>
          <button type="button" className="button button--primary" onClick={() => setProfileModalOpen(true)}><Icon name="settings" /> Edytuj dane</button>
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
                  <PortfolioImage tone={item.coverTone} src={item.coverUrl} />
                  <div>
                    <strong>{item.title}</strong>
                    <small>{dateRange(item)} · {realizationLocation(item, project)}</small>
                    <p>{item.galleryUrls?.length || 0} zdjęć</p>
                    <div className="ic-public-tags">{realizationScope(item, profile).slice(0, 4).map((tag) => <span key={tag}>{tag}</span>)}</div>
                  </div>
                  <span className={`ic-portfolio-status ic-portfolio-status--${item.status}`}>{statusLabel(item.status)}</span>
                  <div className="ic-portfolio-actions">
                    <button type="button" onClick={() => openEdit(item)}>Edytuj</button>
                    <button type="button" onClick={() => setPreviewRealization(item)}>Podgląd</button>
                    <button type="button" onClick={() => toggleItemStatus(item)}>{item.status === "published" ? "Przenieś do szkiców" : "Opublikuj"}</button>
                    {item.status === "published" && <button type="button" onClick={() => copyLink(item)}>Kopiuj link</button>}
                    <button type="button" className="danger" onClick={() => setDeleteTarget({ kind: "realization", id: item.id, title: item.title })}>{item.status === "published" ? "Usuń z wizytówki" : "Usuń"}</button>
                  </div>
                </article>
              );
            })}
          </div>
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
            <button type="button" className="icon-button" onClick={() => copyLink()} aria-label="Kopiuj link"><Icon name={copied ? "check" : "link"} /></button>
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
                        <PortfolioImage tone={item.coverTone} src={item.coverUrl} large />
                        <div>
                          <strong>{item.title}</strong>
                          <span>{formatDate(item.dateEnd || item.updatedAt.slice(0, 10))} · {realizationLocation(item, project)}</span>
                          <div className="ic-public-tags">{realizationScope(item, profile).slice(0, 3).map((tag) => <span key={tag}>{tag}</span>)}</div>
                          <small>{item.status === "published" ? "Opublikowana" : "Szkic"} · {item.galleryUrls?.length || 0} zdjęć</small>
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </section>

            <aside className="ic-portfolio-profile panel">
              <button type="button" className="text-button" onClick={() => setProfileModalOpen(true)}>Edytuj dane wyświetlane</button>
              <span className="ic-portfolio-profile__avatar"><Icon name={profile.avatarIcon} size={54} /></span>
              <h2>{profile.displayName}</h2>
              <p>{profile.publicDescription}</p>
              <div>{profile.tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
              <footer>
                <p><Icon name="phone" /> {profile.publicPhone || "Telefon nieudostępniony"}</p>
                <p><Icon name="link" /> {profile.publicEmail || "E-mail nieudostępniony"}</p>
              </footer>
            </aside>
          </div>

          <ReviewsSection
            reviews={reviews}
            onAdd={() => openReview(null)}
            onEdit={openReview}
            onToggle={toggleReviewStatus}
            onDelete={(review) => setDeleteTarget({ kind: "review", id: review.id, title: review.clientName })}
          />

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
      {reviewModalOpen && (
        <ReviewModal
          editing={editingReview}
          realizations={items}
          onClose={() => { setReviewModalOpen(false); setEditingReview(null); }}
          onSave={upsertReview}
        />
      )}
      {profileModalOpen && (
        <ProfileModal
          profile={profile}
          onClose={() => setProfileModalOpen(false)}
          onSave={(nextProfile) => {
            setProfile(nextProfile);
            setProfileModalOpen(false);
          }}
        />
      )}
      {previewRealization && (
        <RealizationPreviewModal
          item={previewRealization}
          project={projectsById.get(previewRealization.projectId)}
          profile={profile}
          onClose={() => setPreviewRealization(null)}
          onContact={() => {
            setPreviewRealization(null);
            setContactModalOpen(true);
          }}
        />
      )}
      {contactModalOpen && <ContactModal profile={profile} onClose={() => setContactModalOpen(false)} />}
      {deleteTarget && (
        <ConfirmDeleteModal
          target={deleteTarget}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={deleteSelectedTarget}
        />
      )}
    </div>
  );
}
