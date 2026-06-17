import type { User, WorkerProfile } from "./types";
import { isInvestor } from "./access";

export const profileLabels: Record<NonNullable<User["profile_type"]>, string> = {
  company_owner: "Szef firmy",
  investor: "Inwestor",
  independent_contractor: "Samodzielny majster",
  company_worker: "Majster - członek firmy",
  worker: "Majster - członek firmy",
};

export function peopleLabelsForUser(user?: User) {
  if (isInvestor(user)) {
    return {
      section: "Wykonawcy",
      addAction: "Dodaj wykonawcę",
      singular: "Wykonawca",
      assignment: "Wykonawca",
      assignAction: "Przypisz wykonawcę",
    };
  }
  return {
    section: "Majstrowie i ekipy",
    addAction: "Dodaj majstra / ekipę",
    singular: "Majster / ekipa",
    assignment: "Majster / ekipa",
    assignAction: "Przypisz majstra / ekipę",
  };
}

export function workerKindLabel(worker: WorkerProfile): string {
  return worker.profile_kind === "crew" ? "Ekipa" : "Majster";
}

export function workerKindLabelForUser(user: User | undefined, worker: WorkerProfile): string {
  if (isInvestor(user)) return worker.profile_kind === "crew" ? "Firma / ekipa zewnętrzna" : "Wykonawca";
  return workerKindLabel(worker);
}
