import type { SVGProps } from "react";

const paths: Record<string, React.ReactNode> = {
  home: <><path d="m3 11 9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/></>,
  clipboard: <><rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 4V2h6v2M9 10h6M9 14h6M9 18h4"/></>,
  camera: <><path d="M4 7h3l2-3h6l2 3h3v12H4z"/><circle cx="12" cy="13" r="4"/></>,
  mic: <><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"/></>,
  alert: <><path d="M12 3 2.5 20h19z"/><path d="M12 9v5M12 17h.01"/></>,
  send: <><path d="m3 11 18-8-7 18-3-7z"/><path d="m11 14 4-4"/></>,
  search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></>,
  bookmark: <path d="M6 4h12v18l-6-4-6 4z"/>,
  "map-pin": <><path d="M12 21s7-5.2 7-11a7 7 0 0 0-14 0c0 5.8 7 11 7 11Z"/><circle cx="12" cy="10" r="2.5"/></>,
  star: <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9z"/>,
  building: <><rect x="4" y="3" width="16" height="18" rx="1"/><path d="M8 7h.01M12 7h.01M16 7h.01M8 11h.01M12 11h.01M16 11h.01M8 15h.01M12 15h.01M16 15h.01"/></>,
  filter: <><path d="M3 5h18"/><path d="M6 12h12"/><path d="M10 19h4"/></>,
  plus: <path d="M12 5v14M5 12h14"/>,
  users: <><circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M3 21v-2a6 6 0 0 1 12 0v2M15 15a5 5 0 0 1 6 4v2"/></>,
  report: <><path d="M6 2h9l4 4v16H6z"/><path d="M14 2v5h5M9 12h6M9 16h6"/></>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></>,
  close: <path d="m5 5 14 14M19 5 5 19"/>,
  back: <path d="m15 18-6-6 6-6"/>,
  link: <><path d="M10 13a5 5 0 0 0 7.5.5l2-2a5 5 0 0 0-7-7l-1.2 1.2"/><path d="M14 11a5 5 0 0 0-7.5-.5l-2 2a5 5 0 0 0 7 7l1.2-1.2"/></>,
  phone: <><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.4 19.4 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1.9.3 1.7.6 2.5a2 2 0 0 1-.5 2.1L8 9.5a16 16 0 0 0 6.5 6.5l1.2-1.2a2 2 0 0 1 2.1-.5c.8.3 1.6.5 2.5.6a2 2 0 0 1 1.7 2Z"/></>,
  sync: <><path d="M20 7h-5V2"/><path d="M20 7a8 8 0 0 0-14-2M4 17h5v5"/><path d="M4 17a8 8 0 0 0 14 2"/></>,
  check: <path d="m5 12 4 4L19 6"/>,
  menu: <path d="M4 6h16M4 12h16M4 18h16"/>,
  image: <><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-5-5L5 20"/></>,
  wrench: <><path d="M14.7 6.3a4 4 0 0 0-5 5L3 18l3 3 6.7-6.7a4 4 0 0 0 5-5l-2.8 2.8-2.8-2.8z"/></>,
  hammer: <><path d="m15 12-8.5 8.5a2.1 2.1 0 0 1-3-3L12 9"/><path d="m14 3 7 7-3 3-7-7z"/><path d="m5 7 3-3 3 3-3 3z"/></>,
  paint: <><path d="M3 6h13v6H3z"/><path d="M16 8h3a2 2 0 0 1 2 2v1a2 2 0 0 1-2 2h-5"/><path d="M7 12v8a2 2 0 0 0 4 0v-8"/></>,
  brush: <><path d="M18 3c-4 1-7 4-8 8"/><path d="M9 12c-3 0-5 2-5 5 0 2 1 4 4 4 3 0 5-2 5-5"/><path d="m14 7 3 3"/></>,
  pipe: <><path d="M5 4v8a7 7 0 0 0 14 0V4"/><path d="M5 8h14"/><path d="M9 20v2M15 20v2"/></>,
  electric: <path d="m13 2-8 12h6l-1 8 9-13h-6z"/>,
  tile: <><rect x="3" y="3" width="8" height="8"/><rect x="13" y="3" width="8" height="8"/><rect x="3" y="13" width="8" height="8"/><rect x="13" y="13" width="8" height="8"/></>,
  kitchen: <><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M4 9h16M9 3v18M14 13h2"/></>,
  leaf: <><path d="M5 21c8-2 13-8 14-18C9 4 3 10 5 21Z"/><path d="M5 21c3-6 7-10 14-18"/></>,
  broom: <><path d="M16 3 8 11"/><path d="M6 13l5 5"/><path d="M5 14c-2 2-2 5-1 7 3 1 6 1 8-1z"/></>,
  laptop: <><rect x="5" y="4" width="14" height="10" rx="1"/><path d="M3 20h18l-2-4H5z"/></>,
  car: <><path d="M5 17h14l1-6-3-5H7l-3 5z"/><circle cx="7" cy="17" r="2"/><circle cx="17" cy="17" r="2"/><path d="M6 11h12"/></>,
  tools: <><path d="m14 7 3-3 3 3-3 3z"/><path d="m2 22 7-7"/><path d="m16 10 6 6-3 3-6-6"/><path d="M8 3 3 8l4 4 5-5z"/></>,
};

export function Icon({
  name,
  size = 24,
  ...props
}: { name: keyof typeof paths; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {paths[name]}
    </svg>
  );
}
