export type User = {
  id: string;
  email: string;
  name: string;
  public_profile_name?: string;
  phone?: string;
  is_admin: boolean;
  locale: string;
  profile_type?: "company_owner" | "independent_contractor" | "investor" | "company_worker" | "worker";
  preferred_mode: "expanded" | "field";
  beta_access: boolean;
  workspaces: Workspace[];
};

export type PublicProfileOwnerType = "independent_contractor" | "company";

export type PublicProfileRealizationStatus = "draft" | "published";

export type PublicProfileRealization = {
  id: string;
  owner_type: PublicProfileOwnerType;
  owner_id: string;
  project_id?: string | null;
  title: string;
  public_description: string;
  location_public: string;
  work_scope: string[];
  completion_date?: string | null;
  amount?: string | null;
  currency?: string | null;
  show_amount: boolean;
  status: PublicProfileRealizationStatus;
  cover_image_url: string;
  gallery_image_urls: string[];
  sort_order: number;
  published_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type PublicProfile = {
  id: string;
  owner_type: PublicProfileOwnerType;
  owner_id: string;
  display_name: string;
  public_description: string;
  contact_phone: string;
  contact_email: string;
  specializations: string[];
  service_area: string;
  is_public: boolean;
  slug: string;
  created_at: string;
  updated_at: string;
  realizations?: PublicProfileRealization[];
};

export type JobPostingStatus = "draft" | "published";

export type JobPostingTargetType = "company" | "independent_contractor" | "any";

export type JobPostingInterestStatus = "new" | "contact" | "rejected";

export type JobPostingOfferStatus = "draft" | "sent" | "accepted" | "rejected";

export type EstimateStatus =
  | "draft"
  | "pending_approval"
  | "approved_by_owner"
  | "sent"
  | "accepted"
  | "rejected"
  | "cancelled";

export type EstimateRecipientType = "manual" | "investor" | "client";

export type EstimateSourceType = "manual" | "project" | "job_posting";

export type Estimate = {
  id: string;
  owner_type: PublicProfileOwnerType;
  owner_id: string;
  created_by_id: string;
  approved_by_id?: string | null;
  recipient_type: EstimateRecipientType;
  recipient_name: string;
  recipient_email: string;
  recipient_phone: string;
  source_type: EstimateSourceType;
  source_id?: string | null;
  title: string;
  scope_summary: string;
  assumptions: string;
  estimated_price?: string | null;
  price_note: string;
  planned_start: string;
  planned_end: string;
  status: EstimateStatus;
  share_url?: string | null;
  share_active?: boolean;
  shared_at?: string | null;
  sent_at?: string | null;
  approved_at?: string | null;
  accepted_at?: string | null;
  rejected_at?: string | null;
  created_at: string;
  updated_at: string;
};

export type PublicEstimate = {
  id: string;
  owner: {
    owner_type: PublicProfileOwnerType;
    display_name: string;
  };
  recipient_name: string;
  title: string;
  scope_summary: string;
  assumptions: string;
  estimated_price?: string | null;
  price_note: string;
  planned_start: string;
  planned_end: string;
  status: EstimateStatus;
  sent_at?: string | null;
  accepted_at?: string | null;
  rejected_at?: string | null;
  shared_at?: string | null;
};

export type JobPostingContractorContact = {
  display_name: string;
  owner_type: PublicProfileOwnerType;
  specializations: string[];
  service_area: string;
  contact_phone: string;
  contact_email: string;
  slug: string;
  is_public: boolean;
};

export type JobPostingInterest = {
  id: string;
  job_posting_id: string;
  contractor_owner_type: PublicProfileOwnerType;
  contractor_owner_id: string;
  public_profile_id: string;
  message: string;
  status: JobPostingInterestStatus;
  created_at: string;
  updated_at: string;
  contractor?: JobPostingContractorContact;
};

export type JobPostingOffer = {
  id: string;
  job_posting_id: string;
  interest_id: string;
  contractor_owner_type: PublicProfileOwnerType;
  contractor_owner_id: string;
  public_profile_id: string;
  title: string;
  scope_summary: string;
  assumptions: string;
  estimated_price?: string | null;
  price_note: string;
  planned_start: string;
  planned_end: string;
  status: JobPostingOfferStatus;
  sent_at?: string | null;
  accepted_at?: string | null;
  rejected_at?: string | null;
  created_at: string;
  updated_at: string;
  contractor?: JobPostingContractorContact;
  job_posting?: JobPosting;
};

export type JobInterestContext = {
  owner_type: PublicProfileOwnerType;
  owner_id: string;
  can_submit: boolean;
  reason: string;
  public_profile?: PublicProfile | null;
};

export type JobPosting = {
  id: string;
  investor_id?: string;
  title: string;
  description: string;
  location: string;
  budget_label: string;
  deadline: string;
  specializations: string[];
  current_state_description: string;
  target_contractor_type: JobPostingTargetType;
  status: JobPostingStatus;
  published_at?: string | null;
  created_at: string;
  updated_at: string;
  my_interest?: JobPostingInterest | null;
  my_offer?: JobPostingOffer | null;
  interests?: JobPostingInterest[];
  interest_count?: number;
  offers?: JobPostingOffer[];
  offer_count?: number;
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
  media_type?: "image" | "audio";
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
  author_label?: string;
  guest_label?: string;
  author_type?: "client" | "guest" | "system" | "user";
  intent?: "comment" | "confirm_resolved" | "still_open" | "suggest_solution";
  body: string;
  created_at: string;
};

export type Entry = {
  id: string;
  project_id: string;
  stage?: Stage;
  author?: User;
  author_label?: string;
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
  public_contractor_name?: string;
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
  client_cover_media_id?: string | null;
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
  status: "generating" | "draft" | "published" | "ready" | "failed";
  content: {
    summary?: string;
    generated_by_label?: string;
    filename?: string;
    report_date?: string;
    snapshot?: boolean;
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
  report_date?: string;
  generated_by?: User;
  generated_by_label?: string;
  pdf_url?: string;
  legacy_pdf_url?: string;
  created_at: string;
};

export type ClientLink = {
  active: boolean;
  requires_pin: boolean;
  url: string;
};
