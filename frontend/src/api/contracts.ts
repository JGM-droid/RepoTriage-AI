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