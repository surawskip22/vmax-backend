export type ServiceCategory =
  | "remonty"
  | "instalacje"
  | "zewnetrzne"
  | "ogrod"
  | "sprzatanie"
  | "serwis"
  | "stolarka";

export type ServiceTag = {
  slug: string;
  label: string;
  category: ServiceCategory;
  aliases?: string[];
};

export const serviceCategoryLabels: Record<ServiceCategory, string> = {
  remonty: "Remonty i wykończenia",
  instalacje: "Instalacje",
  zewnetrzne: "Budowa i zewnętrzne",
  ogrod: "Ogród i teren",
  sprzatanie: "Sprzątanie",
  serwis: "Serwis i naprawy",
  stolarka: "Stolarka i meble",
};

export const serviceTags: ServiceTag[] = [
  { slug: "remont-lazienki", label: "Remont łazienki", category: "remonty", aliases: ["łazienka", "płytki", "płytkarz", "glazurnik"] },
  { slug: "remont-mieszkan", label: "Remont mieszkań", category: "remonty", aliases: ["mieszkanie", "remont"] },
  { slug: "wykonczenia-wnetrz", label: "Wykończenia wnętrz", category: "remonty", aliases: ["wykończeniówka", "wnętrza"] },
  { slug: "glazura", label: "Glazura", category: "remonty", aliases: ["płytki", "kafle"] },
  { slug: "bialy-montaz", label: "Biały montaż", category: "remonty", aliases: ["armatura", "sanitarny"] },
  { slug: "malowanie", label: "Malowanie", category: "remonty", aliases: ["farby", "ściany"] },
  { slug: "gladzie", label: "Gładzie", category: "remonty", aliases: ["szpachlowanie"] },
  { slug: "podlogi", label: "Podłogi", category: "remonty", aliases: ["panele", "parkiet"] },
  { slug: "montaz-kuchni", label: "Montaż kuchni", category: "remonty", aliases: ["kuchnia", "zabudowa"] },
  { slug: "hydraulika", label: "Hydraulika", category: "instalacje", aliases: ["wod-kan", "rury"] },
  { slug: "elektryka", label: "Elektryka", category: "instalacje", aliases: ["instalacje elektryczne", "pomiary"] },
  { slug: "ogrzewanie", label: "Ogrzewanie", category: "instalacje", aliases: ["co", "piec"] },
  { slug: "klimatyzacja", label: "Klimatyzacja", category: "instalacje", aliases: ["klima"] },
  { slug: "wentylacja", label: "Wentylacja", category: "instalacje" },
  { slug: "fotowoltaika", label: "Fotowoltaika", category: "instalacje", aliases: ["pv", "panele"] },
  { slug: "elewacje", label: "Elewacje", category: "zewnetrzne" },
  { slug: "dachy", label: "Dachy", category: "zewnetrzne", aliases: ["dekarz"] },
  { slug: "ocieplenie", label: "Ocieplenie", category: "zewnetrzne" },
  { slug: "kostka-brukowa", label: "Kostka brukowa", category: "zewnetrzne", aliases: ["bruk"] },
  { slug: "tarasy", label: "Tarasy", category: "zewnetrzne" },
  { slug: "ogrody", label: "Ogrody", category: "ogrod" },
  { slug: "trawniki", label: "Trawniki", category: "ogrod" },
  { slug: "nawadnianie", label: "Nawadnianie", category: "ogrod" },
  { slug: "pielegnacja-ogrodu", label: "Pielęgnacja ogrodu", category: "ogrod", aliases: ["ogród"] },
  { slug: "ogrodzenia", label: "Ogrodzenia", category: "ogrod" },
  { slug: "sprzatanie-po-remoncie", label: "Sprzątanie po remoncie", category: "sprzatanie", aliases: ["poremontowe"] },
  { slug: "sprzatanie-mieszkan", label: "Sprzątanie mieszkań", category: "sprzatanie" },
  { slug: "sprzatanie-biur", label: "Sprzątanie biur", category: "sprzatanie" },
  { slug: "mycie-okien", label: "Mycie okien", category: "sprzatanie" },
  { slug: "naprawy-awaryjne", label: "Naprawy awaryjne", category: "serwis", aliases: ["awaria"] },
  { slug: "serwis-agd", label: "Serwis AGD", category: "serwis" },
  { slug: "serwis-drzwi-okien", label: "Serwis drzwi i okien", category: "serwis", aliases: ["okna", "drzwi"] },
  { slug: "udraznianie", label: "Udrażnianie", category: "serwis", aliases: ["odpływ", "kanalizacja"] },
  { slug: "meble-na-wymiar", label: "Meble na wymiar", category: "stolarka", aliases: ["stolarz"] },
  { slug: "montaz-mebli", label: "Montaż mebli", category: "stolarka" },
  { slug: "stolarka", label: "Stolarka", category: "stolarka" },
  { slug: "drzwi", label: "Drzwi", category: "stolarka" },
  { slug: "zabudowy", label: "Zabudowy", category: "stolarka" },
];

export function tagBySlug(slug: string): ServiceTag | undefined {
  return serviceTags.find((tag) => tag.slug === slug);
}

export function filterServiceTags(query: string, selected: string[] = []): ServiceTag[] {
  const needle = query.trim().toLowerCase();
  return serviceTags.filter((tag) => {
    if (selected.includes(tag.slug)) return false;
    if (!needle) return true;
    const haystack = [tag.label, serviceCategoryLabels[tag.category], ...(tag.aliases || [])].join(" ").toLowerCase();
    return haystack.includes(needle);
  });
}
