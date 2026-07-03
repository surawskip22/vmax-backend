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
  remonty: "Remonty i wykonczenia",
  instalacje: "Instalacje",
  zewnetrzne: "Budowa i zewnetrzne",
  ogrod: "Ogrod i teren",
  sprzatanie: "Sprzatanie",
  serwis: "Serwis i naprawy",
  stolarka: "Stolarka i meble",
};

export const serviceTags: ServiceTag[] = [
  { slug: "remont-lazienki", label: "Remont lazienki", category: "remonty", aliases: ["lazienka", "plytki", "plytkarz", "glazurnik"] },
  { slug: "remont-mieszkan", label: "Remont mieszkan", category: "remonty", aliases: ["mieszkanie", "remont"] },
  { slug: "wykonczenia-wnetrz", label: "Wykonczenia wnetrz", category: "remonty", aliases: ["wykonczeniowka", "wnetrza"] },
  { slug: "glazura", label: "Glazura", category: "remonty", aliases: ["plytki", "kafle"] },
  { slug: "bialy-montaz", label: "Bialy montaz", category: "remonty", aliases: ["armatura", "sanitarny"] },
  { slug: "malowanie", label: "Malowanie", category: "remonty", aliases: ["farby", "sciany"] },
  { slug: "gladzie", label: "Gladzie", category: "remonty", aliases: ["szpachlowanie"] },
  { slug: "podlogi", label: "Podlogi", category: "remonty", aliases: ["panele", "parkiet"] },
  { slug: "montaz-kuchni", label: "Montaz kuchni", category: "remonty", aliases: ["kuchnia", "zabudowa"] },
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
  { slug: "pielegnacja-ogrodu", label: "Pielegnacja ogrodu", category: "ogrod", aliases: ["ogrod"] },
  { slug: "ogrodzenia", label: "Ogrodzenia", category: "ogrod" },
  { slug: "sprzatanie-po-remoncie", label: "Sprzatanie po remoncie", category: "sprzatanie", aliases: ["poremontowe"] },
  { slug: "sprzatanie-mieszkan", label: "Sprzatanie mieszkan", category: "sprzatanie" },
  { slug: "sprzatanie-biur", label: "Sprzatanie biur", category: "sprzatanie" },
  { slug: "mycie-okien", label: "Mycie okien", category: "sprzatanie" },
  { slug: "naprawy-awaryjne", label: "Naprawy awaryjne", category: "serwis", aliases: ["awaria"] },
  { slug: "serwis-agd", label: "Serwis AGD", category: "serwis" },
  { slug: "serwis-drzwi-okien", label: "Serwis drzwi i okien", category: "serwis", aliases: ["okna", "drzwi"] },
  { slug: "udraznianie", label: "Udraznianie", category: "serwis", aliases: ["odplyw", "kanalizacja"] },
  { slug: "meble-na-wymiar", label: "Meble na wymiar", category: "stolarka", aliases: ["stolarz"] },
  { slug: "montaz-mebli", label: "Montaz mebli", category: "stolarka" },
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
