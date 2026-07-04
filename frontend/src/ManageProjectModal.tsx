import { FormEvent, ReactNode, useEffect, useState } from "react";
import {
  canAssignWorkers as canAssignWorkersForUser,
  isCompanyWorker,
  isIndependentContractor,
} from "./access";
import { api } from "./api";
import { Icon } from "./icons";
import { peopleLabelsForUser, workerKindLabelForUser } from "./roleLabels";
import type { Project, User, WorkerProfile } from "./types";

type Toast = { kind: "success" | "error" | "info"; message: string };

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
    rows.push({ label: "Niepewność terminu", value: `+/- ${project.schedule_uncertainty_days} dni` });
  }
  if (amount) rows.push({ label: "Kwota umowna", value: amount });
  return rows;
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

const contractTermsDisclaimer = "To informacja umowna. To nie jest faktura, płatność ani wezwanie do zapłaty.";
const contractTermsReadonlyMessage = "Dane do podglądu - zmienia je szef firmy.";

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
export function ManageProjectModal({
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
  const [tab, setTab] = useState<"details" | "people">("details");
  const [workers, setWorkers] = useState<WorkerProfile[]>([]);
  const canManagePeople = ["owner", "manager"].includes(project.role || "");
  const canManageFinalStatus = canManagePeople && !isCompanyWorker(user);
  const canEditContractTerms = canManagePeople && !isCompanyWorker(user);
  const canAssignWorkers = canAssignWorkersForUser(user, canManagePeople);
  const canShowDetailsLock = canManagePeople && !isIndependentContractor(user);
  const workerLabels = peopleLabelsForUser(user);
  const workerAssignmentLabel = workerLabels.assignment;
  const assignActionLabel = workerLabels.assignAction;
  const workerSectionDescription = user?.profile_type === "investor"
    ? "Wybierz wykonawcę przypisanego do tego zlecenia."
    : "Wybierz majstra lub ekipę przypisaną do tego zlecenia.";

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
      };
      if (canEditContractTerms) {
        payload.planned_start_date = formNullableString(data, "planned_start_date");
        payload.planned_end_date = formNullableString(data, "planned_end_date");
        payload.schedule_uncertainty_days = formOptionalNumber(data, "schedule_uncertainty_days");
        payload.contract_amount = formMoneyString(data, "contract_amount");
      }
      if (canShowDetailsLock) {
        payload.details_locked = data.get("details_locked") === "on";
      }
      if (canManageFinalStatus) {
        payload.status = data.get("status");
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
    <Modal title="Edytuj zlecenie" onClose={onClose} wide>
      <div className="manage-project-shell">
        <div className="manage-tabs">
          <button className={tab === "details" ? "active" : ""} onClick={() => setTab("details")}>Dane</button>
          {canAssignWorkers && <button className={tab === "people" ? "active" : ""} onClick={() => setTab("people")}>Wykonawca</button>}
        </div>
        {tab === "details" && (
          <form className="form-stack job-form" onSubmit={saveDetails}>
          <p className="job-form-intro">
            Zaktualizuj podstawowe dane zlecenia. Zmiany będą widoczne w panelu i w podglądzie klienta.
          </p>
          <section className="job-form-section">
            <header className="job-form-section__header">
              <span><Icon name="clipboard" /></span>
              <div>
                <h3>Dane</h3>
                <p>Nazwa, klient, status i lokalizacja zlecenia.</p>
              </div>
            </header>
            <div className="form-row">
              <label>Nazwa zlecenia<input name="name" defaultValue={project.name} required /></label>
              {canManageFinalStatus && <label>Status<select name="status" defaultValue={project.status}><option value="assigned">Zlecone</option><option value="in_progress">W realizacji</option><option value="completed">Zakończono</option></select></label>}
            </div>
            <div className="form-row">
              <label>Klient<input name="client_name" defaultValue={project.client_name} /></label>
              <label>E-mail klienta<input name="client_email" type="email" defaultValue={project.client_email} /></label>
            </div>
            <label>Adres<input name="address" defaultValue={project.address} /></label>
            <p className="job-form-note">Adres pomaga zorganizować zlecenie. Nie jest widoczny publicznie.</p>
          </section>
          <section className="job-form-section">
            <header className="job-form-section__header">
              <span><Icon name="report" /></span>
              <div>
                <h3>Terminy i budżet</h3>
                <p>Planowane daty, tolerancja terminu i kwota umowna.</p>
              </div>
            </header>
          {canEditContractTerms ? (
            <>
              <div className="form-row">
                <label>Planowany start<input type="date" name="planned_start_date" defaultValue={project.planned_start_date || ""} /></label>
                <label>Planowany koniec<input type="date" name="planned_end_date" defaultValue={project.planned_end_date || ""} /></label>
              </div>
              <div className="form-row">
                <label>Niepewność terminu (+/- dni)<input type="number" name="schedule_uncertainty_days" min="0" step="1" placeholder="np. 3" defaultValue={project.schedule_uncertainty_days ?? ""} /></label>
                <label>Kwota umowna (PLN)<input type="text" name="contract_amount" inputMode="decimal" placeholder="np. 12000" defaultValue={project.contract_amount || ""} /></label>
              </div>
              <p className="job-form-note">{contractTermsDisclaimer}</p>
            </>
          ) : (
            <div className="contract-fields contract-fields--readonly">
              <ContractTermsPanel project={project} />
              {contractTermRows(project).length === 0 && <p className="form-note">Terminy i kwota nie są jeszcze podane.</p>}
              <p className="form-note">{contractTermsReadonlyMessage}</p>
            </div>
          )}
          </section>
          <section className="job-form-section">
            <header className="job-form-section__header">
              <span><Icon name="send" /></span>
              <div>
                <h3>Opis</h3>
                <p>Krótki opis zlecenia i ustaleń.</p>
              </div>
            </header>
            <label>Opis zlecenia<textarea name="description" rows={4} defaultValue={project.description} /></label>
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
          {canShowDetailsLock && (
            <label className="check-label">
              <input type="checkbox" name="details_locked" defaultChecked={project.details_locked} />
              Zablokuj majstrom edycję danych zlecenia. Nadal mogą dodawać zdjęcia, opisy i problemy.
            </label>
          )}
          <div className="modal-actions">
            <Button type="button" variant="secondary" onClick={onClose}>Anuluj</Button>
            <Button type="submit" busy={busy}>Zapisz zmiany</Button>
          </div>
          </form>
        )}
        {tab === "people" && canAssignWorkers && (
          <div className="manage-content manage-content--worker contractor-tab">
          <section className="worker-flow-section contractor-section">
            <header className="contractor-section-header">
              <div>
                <h3>1. Przypisz {user?.profile_type === "investor" ? "wykonawcę" : "majstra / ekipę"}</h3>
                <p>{workerSectionDescription}</p>
              </div>
            </header>
            <form className="worker-assign-form contractor-grid contractor-grid--assign" onSubmit={assignWorker}>
              <label className="contractor-field">
                <span>{workerAssignmentLabel}</span>
                <select name="worker_profile_id" defaultValue={project.worker_profile_id || ""}>
                  <option value="">Bez przypisanego wykonawcy</option>
                  {workers.map((worker) => (
                    <option value={worker.id} key={worker.id}>
                      {workerOptionLabel(user, worker)}
                    </option>
                  ))}
                </select>
              </label>
              <Button type="submit">{assignActionLabel}</Button>
            </form>
            {project.worker_profile ? (
              <div className="assigned-worker-card contractor-current-card">
                <h4>Aktualnie przypisany {user?.profile_type === "investor" ? "wykonawca" : "majster / ekipa"}</h4>
                <article>
                  <span>{project.worker_profile.label.slice(0, 2).toUpperCase()}</span>
                  <div>
                    <strong>{project.worker_profile.label}</strong>
                    <small>{project.worker_profile.email || "Bez e-maila"} · {project.worker_profile.account_type === "account" ? "konto stałe / e-mail" : "link-only"}</small>
                  </div>
                  <b>Przypisany</b>
                </article>
              </div>
            ) : (
              <p className="empty-note">Do tego zlecenia nie przypisano jeszcze wykonawcy.</p>
            )}
            {(project.members?.length || 0) > 0 && (
              <div className="project-owner-list contractor-access-list">
                <h4>Osoby z dostępem</h4>
                {project.members?.map((member) => (
                  <article key={member.id}>
                    <span>{(member.user.name || member.user.email).slice(0, 2).toUpperCase()}</span>
                    <div><strong>{member.user.name || member.user.email}</strong><small>{member.user.email}</small></div>
                    <b>{member.role}</b>
                  </article>
                ))}
              </div>
            )}
          </section>

          <section className="worker-flow-section worker-flow-section--temporary one-time-link-section">
            <header className="contractor-section-header">
              <span className="worker-flow-icon"><Icon name="link" /></span>
              <div>
                <h3>2. Link dla wykonawcy jednorazowego <small>Dostęp tymczasowy</small></h3>
                <p>Wygeneruj tymczasowy link dla wykonawcy jednorazowego. Link pozwala otworzyć tylko to zlecenie i nie tworzy stałego konta. E-mail jest opcjonalny.</p>
              </div>
            </header>
            <form className="temporary-worker-form contractor-grid" onSubmit={createGuest}>
              <label className="contractor-field"><span>Nazwa wykonawcy</span><input name="label" placeholder="np. firma remontowa, glazurnik" required /></label>
              <label className="contractor-field"><span>E-mail wykonawcy (opcjonalnie)</span><input type="email" name="email" placeholder="Możesz zostawić puste" /></label>
              <label className="contractor-field"><span>Uprawnienia</span><select name="permission" defaultValue="history"><option value="add">Tylko dodawanie</option><option value="history">Dodawanie i historia</option><option value="view">Tylko podgląd</option></select></label>
              {workers.length > 0 && (
                <details className="advanced-link-options">
                  <summary>Zaawansowane</summary>
                  <label className="contractor-field"><span>Powiąż z profilem, jeśli dotyczy</span><select name="worker_profile_id" defaultValue={project.worker_profile_id || ""}>
                    <option value="">Nie przypinaj do profilu</option>
                    {workers.map((worker) => (
                      <option value={worker.id} key={worker.id}>
                        {workerOptionLabel(user, worker)}
                      </option>
                    ))}
                  </select></label>
                </details>
              )}
              <Button type="submit" icon="link">Utwórz i skopiuj link</Button>
            </form>
            {guestUrl && <div className="share-result"><input value={guestUrl} readOnly /><Button variant="secondary" onClick={() => void copyToClipboard(guestUrl)}>Kopiuj link</Button></div>}
            {(project.worker_links?.length || 0) > 0 && (
              <div className="temporary-link-list active-links-list">
                <h4>Aktywne linki do tego zlecenia</h4>
                {project.worker_links?.map((link) => (
                  <article key={link.id}>
                    <span>{link.label.slice(0, 2).toUpperCase()}</span>
                    <div>
                      <strong>{link.label}</strong>
                      <small>{link.email || "Bez e-maila"} · link-only · {link.permission === "history" ? "dodawanie i historia" : link.permission === "add" ? "tylko dodawanie" : "tylko podgląd"}</small>
                      {!link.revoked_at && <input className="link-placeholder-input" value="Odśwież link, aby skopiować nowy adres" readOnly />}
                    </div>
                    {!link.revoked_at && <Button type="button" variant="secondary" onClick={() => rotateGuest(link.id)}>Odśwież link</Button>}
                  </article>
                ))}
              </div>
            )}
          </section>
          <div className="modal-actions">
            <Button type="button" variant="secondary" onClick={onClose}>Anuluj</Button>
            <Button type="button" onClick={onClose}>Zapisz zmiany</Button>
          </div>
          </div>
        )}
      </div>
    </Modal>
  );
}


