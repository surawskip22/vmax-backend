import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const appSource = readFileSync(resolve(__dirname, "../src/App.tsx"), "utf8");
const manageProjectModalSource = readFileSync(resolve(__dirname, "../src/ManageProjectModal.tsx"), "utf8");
const accessSource = readFileSync(resolve(__dirname, "../src/access.ts"), "utf8");
const roleLabelsSource = readFileSync(resolve(__dirname, "../src/roleLabels.ts"), "utf8");
const appShellSource = readFileSync(resolve(__dirname, "../src/AppShell.tsx"), "utf8");
const sidebarSource = readFileSync(resolve(__dirname, "../src/RoleAwareSidebar.tsx"), "utf8");
const allSources = [appSource, manageProjectModalSource, accessSource, roleLabelsSource, appShellSource, sidebarSource].join("\n");

function assertIncludes(needle, message) {
  if (!allSources.includes(needle)) {
    throw new Error(message);
  }
}

function assertNotIncludes(needle, message) {
  if (allSources.includes(needle)) {
    throw new Error(message);
  }
}

function assertMatches(pattern, message) {
  if (!pattern.test(allSources)) {
    throw new Error(message);
  }
}

function extractCompanyWorkerNavigation() {
  const match = sidebarSource.match(
    /if \(isCompanyWorker\(user\)\) \{\s*return \[([\s\S]*?)\];\s*\}/,
  );
  if (!match) {
    throw new Error("Nie znaleziono bloku nawigacji dla company_worker.");
  }
  return match[1];
}

assertIncludes("Majstrowie i ekipy", "Brakuje firmowej etykiety ludzi dla szefa.");
assertIncludes("Wykonawcy", "Brakuje inwestorskiej etykiety wykonawcow.");
assertIncludes("Przypisz majstra / ekip\u0119", "Brakuje CTA przypisania majstra/ekipy.");
assertIncludes("Przypisz wykonawc\u0119", "Brakuje CTA przypisania wykonawcy.");

assertMatches(
  /useState<"details" \| "people">\("details"\)/,
  "Modal edycji zlecenia powinien miec tylko wewnetrzne zakladki details/people.",
);
assertIncludes(">Dane</button>", "Modal edycji zlecenia powinien miec zakladke Dane.");
assertIncludes(
  "canAssignWorkers && <button",
  "Zakladka Wykonawca powinna zalezec od canAssignWorkers.",
);
assertIncludes(
  ">Wykonawca</button>",
  "Modal edycji zlecenia powinien miec zakladke Wykonawca.",
);
assertNotIncludes(
  "Link dla wykonawcy tymczasowego",
  "Stara osobna zakladka linku tymczasowego nie powinna wracac.",
);
assertIncludes(
  "Link dla wykonawcy jednorazowego",
  "Link jednorazowy powinien byc w zakladce Wykonawca.",
);
assertIncludes(
  "Link pozwala otworzy\u0107 tylko to zlecenie",
  "Copy linku jednorazowego powinno tlumaczyc zakres jednego zlecenia.",
);
assertIncludes(
  "nie tworzy sta\u0142ego konta",
  "Copy linku jednorazowego powinno tlumaczyc brak stalego konta.",
);
assertIncludes(
  "E-mail jest opcjonalny",
  "Copy linku jednorazowego powinno tlumaczyc opcjonalny e-mail.",
);

assertMatches(
  /function canManagePeople\(user\?: RoleUser\): boolean \{\s*return Boolean\(user && !isIndependentContractor\(user\) && !isCompanyWorker\(user\)\);\s*\}/,
  "canManagePeople musi wykluczac samodzielnego majstra i company_worker.",
);
if (!/function canAssignWorkers\(user: RoleUser, canManageProject: boolean\): boolean \{\s*return canManageProject && !isIndependentContractor\(user\) && !isCompanyWorker\(user\);\s*\}/.test(accessSource)) {
  throw new Error("Helper canAssignWorkers nie powinien dawac company_worker zarzadzania wykonawca.");
}
if (!/const canAssignWorkers = canAssignWorkersForUser\(user, canManagePeople\);/.test(manageProjectModalSource)) {
  throw new Error("ManageProjectModal powinien korzystac z helpera canAssignWorkers.");
}
assertIncludes(
  "assignAction: \"Przypisz wykonawcę\"",
  "Labelka przypisania wykonawcy powinna byc w roleLabels.",
);
assertIncludes(
  "assignAction: \"Przypisz majstra / ekipę\"",
  "Labelka przypisania majstra/ekipy powinna byc w roleLabels.",
);
assertIncludes(
  "section: \"Wykonawcy\"",
  "Inwestorska etykieta panelu ludzi powinna byc w roleLabels.",
);
assertIncludes(
  "section: \"Majstrowie i ekipy\"",
  "Firmowa etykieta panelu ludzi powinna byc w roleLabels.",
);
assertIncludes(
  "profileLabels",
  "Labelki profili powinny zostac w jednym zrodle prawdy.",
);
if (/const profileLabels: Record/.test(appSource)) {
  throw new Error("profileLabels nie powinno wracac do App.tsx.");
}
if (/function peopleLabelsForUser/.test(appSource)) {
  throw new Error("peopleLabelsForUser nie powinno wracac do App.tsx.");
}
if (/function isCompanyWorker/.test(appSource)) {
  throw new Error("Helpery rol nie powinny wracac do App.tsx.");
}
if (!/function getNavigationForUser\(user: User\): NavItem\[\]/.test(sidebarSource)) {
  throw new Error("RoleAwareSidebar powinien zawierac role-aware nawigacje.");
}
if (!/export function visibleSectionForUser\(user: User, section: string\): SectionId/.test(sidebarSource)) {
  throw new Error("visibleSectionForUser powinno korzystac z tej samej nawigacji co sidebar.");
}
if (!/export function AppShell/.test(appShellSource)) {
  throw new Error("AppShell powinien byc wydzielony do osobnego pliku.");
}
if (!/RoleAwareSidebar/.test(appShellSource)) {
  throw new Error("AppShell powinien uzywac RoleAwareSidebar.");
}

if (!/const resetSessionView = useCallback\(\(\) => \{[\s\S]*?setSelectedProject\(null\);[\s\S]*?setProjects\(\[\]\);[\s\S]*?setSection\("home"\);[\s\S]*?\}, \[\]\);/.test(appSource)) {
  throw new Error("Reset sesji powinien czyscic aktywny projekt, liste projektow i wracac do glownego widoku.");
}
if (!/const enterAuthenticatedApp = useCallback\(\(next: User\) => \{[\s\S]*?resetSessionView\(\);[\s\S]*?setUser\(next\);[\s\S]*?navigate\("\/app"\);/.test(appSource)) {
  throw new Error("Login powinien przechodzic przez resetSessionView, zeby nie odziedziczyc projektu poprzedniego konta.");
}
if (!/onUnavailable=\{\(\) => \{ setSelectedProject\(null\); setSection\("projects"\); \}\}/.test(appSource)) {
  throw new Error("Niedostepny projekt po relogu powinien wracac do listy zamiast zostawiac ekran bledu.");
}

assertIncludes(
  "const isGeneratingReport = busyType !== null;",
  "Generowanie raportu PDF powinno miec wspolny guard dla przyciskow daily/final.",
);
assertIncludes(
  "if (generatingRef.current) return;",
  "Drugi szybki klik generowania raportu PDF nie powinien wysylac kolejnego POST.",
);
assertIncludes(
  "generatingRef.current = false;",
  "Guard generowania PDF powinien resetowac sie po sukcesie, bledzie i zmianie projektu.",
);
assertIncludes(
  "disabled={isGeneratingReport || loading}",
  "Przyciski generowania PDF powinny byc disabled podczas generowania albo odswiezania raportow.",
);
assertIncludes(
  "onRefresh={() => loadReports(project)}",
  "Panel wygenerowanych PDF powinien odswiezac tylko raporty, a nie caly projekt.",
);
assertIncludes(
  "setReportError(\"\");",
  "Zmiana projektu i poprawne odswiezenie powinny czyscic lokalny blad raportow.",
);

const companyWorkerNavigation = extractCompanyWorkerNavigation();
if (companyWorkerNavigation.includes('id: "team"')) {
  throw new Error("company_worker nie powinien widziec panelu ludzi.");
}

const advancedIndex = manageProjectModalSource.indexOf("<summary>Zaawansowane</summary>");
const profileLinkIndex = manageProjectModalSource.indexOf("Powi\u0105\u017c z profilem, je\u015bli dotyczy");
if (advancedIndex < 0 || profileLinkIndex < 0 || profileLinkIndex < advancedIndex) {
  throw new Error("Pole Powiaz z profilem powinno byc schowane w Zaawansowane.");
}

console.log("Frontend source regression checks passed.");
