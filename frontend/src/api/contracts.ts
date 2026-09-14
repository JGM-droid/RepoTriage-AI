export type ServiceStatus = {
  service: "repotriage-api";
  status: "healthy" | "ready" | "unavailable";
};

export type IssueRepositorySummary = {
  id: string;
  name: string;
  source_url: string;
};

export type IssueListItem = {
  id: string;
  external_number: number;
  title: string;
  state: string;
  source_url: string;
  created_at: string;
  updated_at: string;
  repository: IssueRepositorySummary;
};

export type IssueDetail = IssueListItem & {
  body: string;
};

export type IssueListResponse = {
  issues: IssueListItem[];
};

export type TriageEvidenceItem = {
  field: string;
  excerpt: string;
  source_url: string;
};

export type TriageClassification = {
  label: string;
  matched_rule: string;
  matched_keywords: string[];
};

export type TriageAssessment = {
  severity: string;
  rationale: string;
};

export type TriageProposedAction = {
  action: string;
  rationale: string;
};

export type TriageHumanReview = {
  recommendation_status: string;
  human_review_status: string;
  decision: string | null;
};

export type TriageStatusEvent = {
  status: TriageStatus;
  created_at: string;
};

export type TriageStatus = "queued" | "running" | "completed" | "failed";

export type TriageResult = {
  analysis_id: string;
  issue_id: string;
  status: TriageStatus;
  classification: TriageClassification | null;
  evidence: TriageEvidenceItem[];
  assessment: TriageAssessment | null;
  proposed_action: TriageProposedAction | null;
  human_review: TriageHumanReview | null;
  status_history: TriageStatusEvent[];
  created_at: string;
  updated_at: string;
};