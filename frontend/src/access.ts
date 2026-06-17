import type { User } from "./types";

type RoleUser = Pick<User, "profile_type"> | undefined;

export function isCompanyWorker(user?: RoleUser): boolean {
  return user?.profile_type === "company_worker" || user?.profile_type === "worker";
}

export function isCompanyOwner(user?: RoleUser): boolean {
  return user?.profile_type === "company_owner";
}

export function isInvestor(user?: RoleUser): boolean {
  return user?.profile_type === "investor";
}

export function isIndependentContractor(user?: RoleUser): boolean {
  return user?.profile_type === "independent_contractor";
}

export function canManagePeople(user?: RoleUser): boolean {
  return Boolean(user && !isIndependentContractor(user) && !isCompanyWorker(user));
}

export function canCreateProject(user?: RoleUser): boolean {
  return Boolean(user && !isCompanyWorker(user));
}

export function canSeeTeamPanel(user?: RoleUser): boolean {
  return canManagePeople(user);
}

export function canAssignWorkers(user: RoleUser, canManageProject: boolean): boolean {
  return canManageProject && !isIndependentContractor(user) && !isCompanyWorker(user);
}
