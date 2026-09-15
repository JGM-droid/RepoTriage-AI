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

export type TriageAIInference = {
  narrative: string;
  status: "succeeded" | "fallback";
  provider: string;
  model: string;
  prompt_name: string;
  input_tokens: number;
  output_tokens: number;
  estimated_cost_usd: number;
  fallback_reason: string | null;
};

export type TriageDecision = "approve" | "reject" | "request_revision";

export type TriageHumanReview = {
  recommendation_status: string;
  human_review_status: string;
  decision: TriageDecision | null;
  decided_by: string | null;
  decided_at: string | null;
  rationale: string | null;
};

export type TriageStatusEvent = {
  status: TriageStatus;
  created_at: string;
};

export type TriageStatus =
  | "queued"
  | "running"
  | "retrying"
  | "completed"
  | "failed"
  | "timed_out";

export type TriageStageAttempt = {
  stage: string;
  attempt_number: number;
  status: string;
  error: string | null;
  created_at: string;
};

export type TriageResult = {
  analysis_id: string;
  issue_id: string;
  status: TriageStatus;
  current_stage: string | null;
  attempt_count: number;
  classification: TriageClassification | null;
  evidence: TriageEvidenceItem[];
  assessment: TriageAssessment | null;
  proposed_action: TriageProposedAction | null;
  ai_inference: TriageAIInference | null;
  human_review: TriageHumanReview | null;
  status_history: TriageStatusEvent[];
  stage_attempts: TriageStageAttempt[];
  created_at: string;
  updated_at: string;
};

export type TriageStartedResponse = {
  analysis_id: string;
  issue_id: string;
  status: TriageStatus;
  poll_url: string;
};