export type User = {
  id: string;
  email: string;
  name: string;
  phone?: string;
  is_admin: boolean;
  locale: string;
  profile_type?: "company_owner" | "independent_contractor" | "investor" | "company_worker" | "worker";
  preferred_mode: "expanded" | "field";
  beta_access: boolean;
  workspaces: Workspace[];
};

export type Workspace = {
  id: string;
  name: string;
  kind: string;
  role: string;
  description?: string;
  phone?: string;
  address?: string;
  members?: Array<{ id: string; role: string; user: User }>;
  worker_profiles?: WorkerProfile[];
  worker_links?: WorkerLink[];
};

export type WorkerProfile = {
  id: string;
  label: string;
  profile_kind: "craftsman" | "crew";
  email: string;
  phone: string;
  note: string;
  workspace_id?: string;
  active: boolean;
  account_type: "account" | "link_only";
  account_status: "active" | "pending_email" | "email_missing_invite" | "link_only";
  display_type: string;
  assigned_projects: Array<{ id: string; name: string; status: string }>;
  created_at: string;
  updated_at: string;
};

export type WorkerLink = {
  id: string;
  label: string;
  email: string;
  kind: "guest" | "worker";
  account_type: "account" | "link_only";
  permission: "add" | "history" | "view";
  project_id: string;
  project_name?: string;
  worker_profile_id?: string;
  expires_at?: string;
  revoked_at?: string;
  created_at: string;
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
  purpose: "attachment" | "voice_description" | "voice_note";
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
  planned_start_date?: string | null;
  planned_end_date?: string | null;
  schedule_uncertainty_days?: number | null;
  contract_amount?: string | null;
  contract_currency?: string | null;
  workspace_id?: string;
  worker_profile_id?: string;
  worker_profile?: WorkerProfile;
  role?: string;
  stages?: Stage[];
  members?: Array<{ id: string; role: string; user: User }>;
  worker_links?: WorkerLink[];
  entry_count?: number;
  open_problem_count?: number;
  portfolio_enabled: boolean;
  portfolio_slug?: string;
  portfolio_summary: string;
  details_locked: boolean;
  can_edit_details?: boolean;
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

export type ClientLink = {
  active: boolean;
  requires_pin: boolean;
  url: string;
};
