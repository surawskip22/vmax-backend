export type User = {
  id: string;
  email: string;
  name: string;
  phone?: string;
  is_admin: boolean;
  locale: string;
  beta_access: boolean;
  workspaces: Workspace[];
};

export type Workspace = {
  id: string;
  name: string;
  kind: string;
  role: string;
};

export type Stage = {
  id: string;
  title: string;
  position: number;
  status: "planned" | "active" | "completed";
};

export type MediaAsset = {
  id: string;
  kind: "image" | "audio";
  original_name: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  status: string;
  url: string;
  created_at: string;
};

export type Comment = {
  id: string;
  author?: User;
  guest_label?: string;
  body: string;
  created_at: string;
};

export type Entry = {
  id: string;
  project_id: string;
  stage?: Stage;
  author?: User;
  guest_label?: string;
  kind: "update" | "problem";
  body: string;
  transcript: string;
  ai_summary: string;
  occurred_at: string;
  problem_status?: "open" | "resolved";
  media: MediaAsset[];
  comments: Comment[];
  created_at: string;
};

export type Project = {
  id: string;
  name: string;
  client_name: string;
  client_email: string;
  address: string;
  description: string;
  status: string;
  template: string;
  workspace_id?: string;
  role?: string;
  stages?: Stage[];
  members?: Array<{ id: string; role: string; user: User }>;
  entry_count?: number;
  open_problem_count?: number;
  portfolio_enabled: boolean;
  portfolio_slug?: string;
  portfolio_summary: string;
  created_at: string;
  updated_at: string;
  guest?: { label: string; permission: string };
};

export type Report = {
  id: string;
  project_id: string;
  title: string;
  report_type: string;
  status: "generating" | "draft" | "published" | "failed";
  content: {
    summary?: string;
    stages?: Array<{
      title: string;
      entries: Array<{
        entry_id: string;
        date: string;
        text: string;
        kind: string;
        problem_status?: string;
        media_ids?: string[];
      }>;
    }>;
    problems?: unknown[];
  };
  published_at?: string;
  pdf_url?: string;
  created_at: string;
};
