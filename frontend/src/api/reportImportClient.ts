import { request, requestFormData } from "./client.ts";

export type ReportImportMode = "migration" | "roundtrip";
export type ReportImportStatus =
  | "uploaded"
  | "parsing"
  | "preview_ready"
  | "confirming"
  | "succeeded"
  | "failed";
export type ReportImportSeverity = "info" | "warning" | "error";
export type ReportImportConfidence = "exact" | "high" | "ambiguous" | "unmapped";
export type ReportImportResolutionAction = "adopt_candidate" | "keep_original" | "skip";
export type ReportImportAppendixASource = "document" | "existing_project";

export type ReportImportFingerprint = {
  sha256: string;
  table_count: number;
  section_count: number;
  top_level_table_columns: number[];
  heading_matches: string[];
  matched: boolean;
};

export type ReportImportChapterStat = {
  key?: string;
  title?: string;
  paragraph_count?: number;
  table_count?: number;
  image_count?: number;
  [key: string]: unknown;
};

export type ReportImportSummary = {
  template_match: boolean | Record<string, unknown>;
  chapter_stats: ReportImportChapterStat[] | Record<string, unknown>;
  automatic_mappings: number | unknown[] | Record<string, unknown>;
  pending_confirmation: number | unknown[] | Record<string, unknown>;
  unmapped_content: number | unknown[] | Record<string, unknown>;
  appendix_sources: unknown[] | Record<string, unknown>;
};

export type ReportImportIssue = {
  id: number;
  revision: number;
  code: string;
  severity: ReportImportSeverity;
  association_id?: string | null;
  authority_field_id?: string | null;
  field_path?: string | null;
  source_locator: string;
  original_text: string;
  original_text_truncated: boolean;
  source_value_hash: string;
  candidate_value?: unknown;
  confidence: ReportImportConfidence;
  status: "open" | "resolved" | "ignored";
  needs_confirmation: boolean;
  blocks_confirmation: boolean;
  blocks_final_export: boolean;
  created_at: string;
  updated_at: string;
};

export type ReportImportResolution = {
  id: number;
  issue_id: number;
  issue_revision: number;
  association_id?: string | null;
  authority_field_id?: string | null;
  field_path?: string | null;
  action: ReportImportResolutionAction;
  resolved_value?: unknown;
  resolved_by_user: boolean;
  applied: boolean;
  created_at: string;
  updated_at: string;
};

export type ReportImportJob = {
  id: number;
  status: ReportImportStatus | string;
  mode: ReportImportMode;
  job_revision: number;
  original_name: string;
  detected_edition?: string | null;
  detected_revision?: string | null;
  fingerprint: ReportImportFingerprint;
  summary: ReportImportSummary;
  issues: ReportImportIssue[];
  resolutions: ReportImportResolution[];
  appendix_a_source?: ReportImportAppendixASource | null;
  confirmable: boolean;
  created_project_uuid?: string | null;
  created_project_updated_at?: string | null;
  error_message?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
};

export type ReportImportResolutionInput = {
  issue_id: number;
  revision: number;
  action: ReportImportResolutionAction;
  resolved_value?: unknown;
};

export type ReportImportConfirmInput = {
  job_revision: number;
  project_name: string;
  appendix_a_source: ReportImportAppendixASource;
  appendix_a_project_uuid?: string;
  accepted_resolutions?: number[];
  keep_unresolved_original: true;
};

export function uploadReportImport(file: File, mode: ReportImportMode = "migration"): Promise<ReportImportJob> {
  const form = new FormData();
  form.append("file", file);
  return requestFormData<ReportImportJob>(
    `/api/report-imports/docx?mode=${encodeURIComponent(mode)}`,
    form
  );
}

export function getReportImport(jobId: number): Promise<ReportImportJob> {
  return request<ReportImportJob>(`/api/report-imports/${jobId}`);
}

export function updateReportImportResolutions(
  jobId: number,
  jobRevision: number,
  resolutions: ReportImportResolutionInput[],
  expectedProjectUpdatedAt?: string | null
): Promise<ReportImportJob> {
  const projectRevision = expectedProjectUpdatedAt
    ? { expected_project_updated_at: expectedProjectUpdatedAt }
    : {};
  return request<ReportImportJob>(`/api/report-imports/${jobId}/resolutions`, {
    method: "PUT",
    body: JSON.stringify({ job_revision: jobRevision, ...projectRevision, resolutions })
  });
}

export function confirmReportImport(
  jobId: number,
  payload: ReportImportConfirmInput
): Promise<ReportImportJob> {
  return request<ReportImportJob>(`/api/report-imports/${jobId}/confirm`, {
    method: "POST",
    body: JSON.stringify(payload)
  });
}
