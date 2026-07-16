import { request, requestFormData } from "./client.ts";

export type ReportRoundtripJobStatus =
  | "uploaded"
  | "validating"
  | "invalid"
  | "diff_ready"
  | "conflicts_pending"
  | "ready_to_commit"
  | "committing"
  | "succeeded"
  | "failed"
  | "stale";

export type ReportRoundtripResolutionAction = "keep_database" | "apply_word";

export type ReportRoundtripJob = {
  id: number | string;
  project_uuid: string;
  mode: "roundtrip";
  status: ReportRoundtripJobStatus;
  original_name: string;
  base_project_revision: number;
  observed_project_revision: number;
  source_snapshot_id?: number | string | null;
  source_docx_hash?: string | null;
  manifest_hash?: string | null;
  source_snapshot_hash?: string | null;
  writable_contract_hash?: string | null;
  diff_hash?: string | null;
  resolution_hash?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  committed_at?: string | null;
};

export type ReportRoundtripIssue = {
  severity: "error" | "warning" | "info";
  code: string;
  message: string;
  blocks_progress?: boolean;
  phase?: string | null;
  field_id?: string | null;
  field_path?: string | null;
  row_id?: string | null;
  section_code?: string | null;
  object_name?: string | null;
  remediation?: string | null;
  three_way_summary?: {
    base?: string | null;
    database?: string | null;
    word?: string | null;
  } | null;
};

export type ReportRoundtripIssueCollection = {
  job_id: number | string;
  status: ReportRoundtripJobStatus;
  errors: ReportRoundtripIssue[];
  warnings: ReportRoundtripIssue[];
  info: ReportRoundtripIssue[];
};

export type ReportRoundtripDiffDisposition =
  | "unchanged"
  | "keep_database"
  | "apply_word"
  | "already_equal"
  | "conflict"
  | "ignored";

export type ReportRoundtripDiffValue =
  | string
  | number
  | boolean
  | null
  | Record<string, unknown>
  | unknown[];

export type ReportRoundtripDiffItem = {
  id: number | string;
  conflict_id?: number | string | null;
  field_path: string;
  field_label?: string | null;
  field_type?: string | null;
  section_code?: string | null;
  section_title?: string | null;
  entity_uuid?: string | null;
  object_name?: string | null;
  row_id?: string | null;
  base_value: ReportRoundtripDiffValue;
  database_value: ReportRoundtripDiffValue;
  word_value: ReportRoundtripDiffValue;
  disposition: ReportRoundtripDiffDisposition;
  resolution?: ReportRoundtripResolutionAction | null;
  ignored_reason?: string | null;
};

export type ReportRoundtripDiffGroup = {
  group_key: string;
  section_code?: string | null;
  section_title?: string | null;
  object_name?: string | null;
  items: ReportRoundtripDiffItem[];
};

export type ReportRoundtripDiffSummary = {
  total: number;
  unchanged: number;
  keep_database: number;
  apply_word: number;
  already_equal: number;
  conflicts: number;
  ignored: number;
};

export type ReportRoundtripDiff = {
  job_id: number | string;
  status: ReportRoundtripJobStatus;
  diff_hash: string;
  base_project_revision: number;
  observed_project_revision: number;
  summary?: Partial<ReportRoundtripDiffSummary>;
  groups?: ReportRoundtripDiffGroup[];
  items?: ReportRoundtripDiffItem[];
  ignored_changes?: ReportRoundtripDiffItem[];
};

export type ReportRoundtripResolutionInput = {
  conflict_id: number | string;
  action: ReportRoundtripResolutionAction;
};

export type ReportRoundtripResolutionResult = {
  job_id: number | string;
  status: ReportRoundtripJobStatus;
  diff_hash: string;
  resolution_hash: string;
  expected_project_revision: number;
  resolved_conflicts: number;
};

export type ReportRoundtripCommitResult = {
  job_id: number | string;
  status: ReportRoundtripJobStatus;
  project_uuid: string;
  before_revision: number;
  after_revision?: number | null;
  resolution_hash: string;
  applied_fields: number;
  kept_fields: number;
  ignored_changes: number;
  error_code?: string | null;
  error_message?: string | null;
};

export function createReportRoundtripJob(projectUuid: string, file: File): Promise<ReportRoundtripJob> {
  const form = new FormData();
  form.append("file", file);
  form.append("mode", "roundtrip");
  return requestFormData<ReportRoundtripJob>(
    `/api/projects/${encodeURIComponent(projectUuid)}/report-import-jobs`,
    form
  );
}

export function getReportRoundtripJob(jobId: number | string): Promise<ReportRoundtripJob> {
  return request<ReportRoundtripJob>(`/api/report-import-jobs/${encodeURIComponent(String(jobId))}`);
}

export function getReportRoundtripDiff(jobId: number | string): Promise<ReportRoundtripDiff> {
  return request<ReportRoundtripDiff>(`/api/report-import-jobs/${encodeURIComponent(String(jobId))}/diff`);
}

export function getReportRoundtripIssues(jobId: number | string): Promise<ReportRoundtripIssueCollection> {
  return request<ReportRoundtripIssueCollection>(`/api/report-import-jobs/${encodeURIComponent(String(jobId))}/issues`);
}

export function updateReportRoundtripResolution(
  jobId: number | string,
  payload: {
    diff_hash: string;
    expected_project_revision: number;
    resolutions: ReportRoundtripResolutionInput[];
  }
): Promise<ReportRoundtripResolutionResult> {
  return request<ReportRoundtripResolutionResult>(
    `/api/report-import-jobs/${encodeURIComponent(String(jobId))}/resolution`,
    { method: "PUT", body: JSON.stringify(payload) }
  );
}

export function commitReportRoundtripJob(
  jobId: number | string,
  payload: { resolution_hash: string; expected_project_revision: number }
): Promise<ReportRoundtripCommitResult> {
  return request<ReportRoundtripCommitResult>(
    `/api/report-import-jobs/${encodeURIComponent(String(jobId))}/commit`,
    { method: "POST", body: JSON.stringify(payload) }
  );
}
