import {
  projectCreatePayload,
  projectUpgradeCopyPayload,
  type ProjectCreationOperation,
  type ProjectType,
  type WorkflowStatus
} from "../projectContracts.ts";

const runtimeEnv = import.meta.env ?? {};
const rawExplicitApiBaseUrl = runtimeEnv.VITE_API_BASE_URL?.trim();
const explicitApiBaseUrl = rawExplicitApiBaseUrl?.replace(/\/+$/, "") ?? "";
const API_BASE_URL = rawExplicitApiBaseUrl
  ? explicitApiBaseUrl
  : (runtimeEnv.DEV ? "http://127.0.0.1:8000" : "");

export type Section = {
  id: number;
  project_id: number;
  code: string;
  title: string;
  table_title: string;
  sort_order: number;
};

export type Project = {
  id: number;
  project_uuid: string;
  name: string;
  project_type: ProjectType;
  workflow_status: WorkflowStatus;
  template_package_id?: string | null;
  template_edition?: string | null;
  template_revision?: string | null;
  template_asset_set_hash?: string | null;
  source_project_uuid?: string | null;
  created_by_operation: ProjectCreationOperation;
  created_at: string;
  updated_at: string;
  sections: Section[];
};

export type MetricResult = {
  d?: string | null;
  a?: string | null;
  k?: string | null;
  ra?: string | null;
  rk?: string | null;
  object_score?: string | null;
  unit_score?: string | null;
  compliance?: string | null;
};

export type AssessmentRow = {
  id: number;
  section_id: number;
  assessment_object_uuid?: string | null;
  unit: string;
  object_name: string;
  subsystem: string;
  record_text: string;
  sort_order: number;
  metric_result: MetricResult;
};

export type AssessmentRowInput = {
  id?: number | null;
  unit: string;
  object_name: string;
  subsystem?: string;
  record_text: string;
  sort_order?: number | null;
  metric_result?: MetricResult;
  cross_references?: CrossReferenceInput[];
};

export type EvidenceImage = {
  id: number;
  project_id: number;
  section_code: string;
  file_path: string;
  original_name: string;
  caption: string;
  alt_text: string;
  sort_order: number;
  project_image_no?: number | null;
  pixel_width?: number | null;
  pixel_height?: number | null;
  dpi_x?: number | null;
  dpi_y?: number | null;
  display_width_in?: number | null;
  display_height_in?: number | null;
  created_at: string;
  updated_at: string;
  file_url?: string | null;
  figure_label?: string | null;
  warnings: string[];
};

export type CrossReference = {
  id: number;
  source_row_id: number;
  target_image_id?: number | null;
  token: string;
  display_text: string;
};

export type CrossReferenceInput = {
  target_image_id?: number | null;
  token: string;
  display_text?: string;
};

export type SectionDetail = {
  section: Section;
  rows: AssessmentRow[];
  subsystems: string[];
  evidence_images: EvidenceImage[];
  cross_references: CrossReference[];
};

export type TemplateColumn = {
  key: string;
  label: string;
  width_in: number;
};

export type TemplateProfile = {
  profile_id: string;
  sections: Array<{
    code: string;
    title: string;
    table_title: string;
    table_type: "technical" | "management";
    figure_prefix: string;
    fixed_object_names?: string[];
  }>;
  tables: {
    technical: { columns: TemplateColumn[] };
    management: { columns: TemplateColumn[] };
  };
  content_controls: {
    technical_metric: {
      options: string[];
      default: string;
    };
    management_compliance: {
      options: string[];
      default: string;
    };
  };
};

export type ValidationSummary = {
  errors: number;
  warnings: number;
  info: number;
};

export type ValidationIssue = {
  id?: number | null;
  project_id: number;
  severity: "error" | "warning" | "info";
  code: string;
  message: string;
  target_type?: string | null;
  target_id?: string | null;
  created_at?: string | null;
};

export type ValidationResponse = {
  summary: ValidationSummary;
  issues: ValidationIssue[];
};

export type RenderJob = {
  id: number;
  project_id: number;
  status: "queued" | "running" | "succeeded" | "failed" | "timeout";
  mode: "editable" | "final";
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  output_docx_path?: string | null;
  output_pdf_path?: string | null;
  output_docx_url?: string | null;
  output_pdf_url?: string | null;
  page_count?: number | null;
  log_path?: string | null;
  log_url?: string | null;
  error_message?: string | null;
};


export type DocxImportIssue = {
  severity: "error" | "warning" | "info";
  code: string;
  message: string;
  section_code?: string | null;
  target?: string | null;
};

export type DocxImportSectionPreview = {
  code: string;
  title: string;
  table_title: string;
  table_type: "technical" | "management" | string;
  row_count: number;
  image_count: number;
  reference_count: number;
};

export type DocxImportJob = {
  id: number;
  status: "uploaded" | "parsing" | "preview_ready" | "importing" | "succeeded" | "failed" | string;
  original_name: string;
  source_docx_path: string;
  parsed_json_path?: string | null;
  suggested_project_name: string;
  created_project_id?: number | null;
  sections: DocxImportSectionPreview[];
  summary: Record<string, number>;
  issues: DocxImportIssue[];
  can_create_project: boolean;
  error_message?: string | null;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
};
export type RecordTemplate = {
  id: string;
  source_type?: "system" | "user";
  section_code: string;
  table_type: "technical" | "management";
  unit: string;
  object_name: string;
  title: string;
  record_text: string;
  source_row?: number | null;
  tags?: string[];
  is_enabled?: boolean;
  created_at?: string | null;
  updated_at?: string | null;
};


export type RecordTemplateSlotGroup = "verification_record" | "score_basis";
export type RecordTemplateSlotType = "compliant" | "non_compliant" | "not_applicable" | "fully_compliant" | "score_adjusted";

export type RecordTemplateSlot = {
  id: number;
  section_code: string;
  table_type: "technical" | "management";
  unit: string;
  template_group: RecordTemplateSlotGroup;
  template_group_label: string;
  template_type: RecordTemplateSlotType;
  template_type_label: string;
  title: string;
  record_text: string;
  default_record_text: string;
  tags?: string[];
  is_customized: boolean;
  created_at?: string | null;
  updated_at?: string | null;
};

export type RecordTemplateSlotUpdateInput = {
  title?: string;
  record_text?: string;
  tags?: string[];
};

export type RecordTemplateSlotImportItem = {
  section_code: string;
  table_type: "technical" | "management";
  unit: string;
  template_group: RecordTemplateSlotGroup;
  template_type: RecordTemplateSlotType;
  title: string;
  record_text: string;
  tags?: string[];
};

export type RecordTemplateSlotExport = {
  profile_id: string;
  exported_at: string;
  templates: RecordTemplateSlotImportItem[];
};

export type RecordTemplateSlotImportPayload = {
  profile_id?: string | null;
  exported_at?: string | null;
  templates: RecordTemplateSlotImportItem[];
};

export type RecordTemplateSlotImportResult = {
  summary: RecordTemplateImportSummary;
  items: Array<{
    index: number;
    action: "update" | "skip" | "error" | "create";
    message: string;
    slot_id?: number | null;
    section_code: string;
    unit: string;
    template_group: string;
    template_type: string;
    title: string;
  }>;
};
export type RecordTemplateInput = {
  section_code: string;
  table_type: "technical" | "management";
  unit?: string;
  object_name?: string;
  title?: string;
  record_text: string;
  tags?: string[];
};
export type RecordTemplateExport = {
  profile_id: string;
  exported_at: string;
  templates: Array<RecordTemplateInput & { id?: string | null; template_key?: string | null }>;
};

export type RecordTemplateImportPayload = {
  profile_id?: string | null;
  exported_at?: string | null;
  templates: Array<RecordTemplateInput & { id?: string | null; template_key?: string | null }>;
};

export type RecordTemplateImportSummary = {
  created: number;
  updated: number;
  skipped: number;
  errors: number;
};

export type RecordTemplateImportItem = {
  index: number;
  action: "create" | "update" | "skip" | "error";
  message: string;
  template_id?: string | null;
  section_code: string;
  unit: string;
  object_name: string;
  title: string;
};

export type RecordTemplateImportResult = {
  summary: RecordTemplateImportSummary;
  items: RecordTemplateImportItem[];
};

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });

  if (!response.ok) {
    throw await responseApiError(response, `请求失败：${response.status}`);
  }

  return response.json() as Promise<T>;
}

export async function requestFormData<T>(path: string, form: FormData, method = "POST"): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { method, body: form });
  if (!response.ok) {
    throw await responseApiError(response, `请求失败：${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function downloadFile(path: string, fallbackFileName: string): Promise<string> {
  const response = await fetch(`${API_BASE_URL}${path}`);
  if (!response.ok) {
    throw await responseApiError(response, `下载失败：${response.status}`);
  }

  const blob = await response.blob();
  const fileName = _fileNameFromDisposition(response.headers.get("content-disposition")) ?? fallbackFileName;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return fileName;
}

export function createProject(name: string, projectType: ProjectType = "appendix_a"): Promise<Project> {
  return request<Project>("/api/projects", {
    method: "POST",
    body: JSON.stringify(projectCreatePayload(name, projectType))
  });
}

export function upgradeProjectCopy(
  projectUuid: string,
  name: string,
  idempotencyKey: string
): Promise<Project> {
  return request<Project>(`/api/projects/${encodeURIComponent(projectUuid)}/upgrade-copy`, {
    method: "POST",
    body: JSON.stringify(projectUpgradeCopyPayload(name, idempotencyKey))
  });
}

export function changeProjectWorkflow(
  projectUuid: string,
  action: "ready-for-review" | "reopen"
): Promise<Project> {
  return request<Project>(`/api/projects/${encodeURIComponent(projectUuid)}/workflow/${action}`, {
    method: "POST",
    body: "{}"
  });
}

export function listProjects(): Promise<Project[]> {
  return request<Project[]>("/api/projects");
}

export function getProject(projectId: number): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}`);
}

export function updateProject(projectId: number, name: string): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}`, {
    method: "PUT",
    body: JSON.stringify({ name })
  });
}

export function deleteProject(projectId: number): Promise<Project> {
  return request<Project>(`/api/projects/${projectId}`, {
    method: "DELETE"
  });
}

export function getSectionDetail(projectId: number, code: string): Promise<SectionDetail> {
  return request<SectionDetail>(`/api/projects/${projectId}/sections/${code}`);
}

export function updateSectionDetail(
  projectId: number,
  code: string,
  payload: {
    title?: string | null;
    table_title?: string | null;
    subsystems?: string[] | null;
    rows: AssessmentRowInput[];
  }
): Promise<SectionDetail> {
  return request<SectionDetail>(`/api/projects/${projectId}/sections/${code}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export function importSectionToProject(
  projectId: number,
  code: string,
  targetProjectId: number
): Promise<SectionDetail> {
  return request<SectionDetail>(`/api/projects/${projectId}/sections/${code}/import-to-project`, {
    method: "POST",
    body: JSON.stringify({ target_project_id: targetProjectId })
  });
}

export function getTemplateProfile(): Promise<TemplateProfile> {
  return request<TemplateProfile>("/api/template-profile");
}

export function getRecordTemplates(sectionCode?: string, keyword?: string): Promise<RecordTemplate[]> {
  const params = new URLSearchParams();
  if (sectionCode) {
    params.set("section_code", sectionCode);
  }
  if (keyword) {
    params.set("keyword", keyword);
  }
  const query = params.toString() ? `?${params.toString()}` : "";
  return request<RecordTemplate[]>(`/api/record-templates${query}`);
}


export function getRecordTemplateSlots(
  sectionCode?: string,
  unit?: string,
  templateGroup?: RecordTemplateSlotGroup,
  templateType?: RecordTemplateSlotType
): Promise<RecordTemplateSlot[]> {
  const params = new URLSearchParams();
  if (sectionCode) {
    params.set("section_code", sectionCode);
  }
  if (unit) {
    params.set("unit", unit);
  }
  if (templateGroup) {
    params.set("template_group", templateGroup);
  }
  if (templateType) {
    params.set("template_type", templateType);
  }
  const query = params.toString() ? `?${params.toString()}` : "";
  return request<RecordTemplateSlot[]>(`/api/record-template-slots${query}`);
}

export function updateRecordTemplateSlot(
  slotId: number,
  payload: RecordTemplateSlotUpdateInput
): Promise<RecordTemplateSlot> {
  return request<RecordTemplateSlot>(`/api/record-template-slots/${slotId}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export function resetRecordTemplateSlot(slotId: number): Promise<RecordTemplateSlot> {
  return request<RecordTemplateSlot>(`/api/record-template-slots/${slotId}/reset`, {
    method: "POST"
  });
}

export function exportRecordTemplateSlots(): Promise<RecordTemplateSlotExport> {
  return request<RecordTemplateSlotExport>("/api/record-template-slots/export");
}

export function previewRecordTemplateSlotImport(
  payload: RecordTemplateSlotImportPayload
): Promise<RecordTemplateSlotImportResult> {
  return request<RecordTemplateSlotImportResult>("/api/record-template-slots/import-preview", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function importRecordTemplateSlots(
  payload: RecordTemplateSlotImportPayload
): Promise<RecordTemplateSlotImportResult> {
  return request<RecordTemplateSlotImportResult>("/api/record-template-slots/import", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}
export function createRecordTemplate(payload: RecordTemplateInput): Promise<RecordTemplate> {
  return request<RecordTemplate>("/api/record-templates", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function updateRecordTemplate(
  templateId: string,
  payload: Partial<RecordTemplateInput>
): Promise<RecordTemplate> {
  return request<RecordTemplate>(`/api/record-templates/${encodeURIComponent(templateId)}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export function deleteRecordTemplate(templateId: string): Promise<RecordTemplate> {
  return request<RecordTemplate>(`/api/record-templates/${encodeURIComponent(templateId)}`, {
    method: "DELETE"
  });
}

export function copyRecordTemplate(templateId: string): Promise<RecordTemplate> {
  return request<RecordTemplate>(`/api/record-templates/${encodeURIComponent(templateId)}/copy`, {
    method: "POST"
  });
}

export function exportRecordTemplates(): Promise<RecordTemplateExport> {
  return request<RecordTemplateExport>("/api/record-templates/export");
}

export function previewRecordTemplateImport(payload: RecordTemplateImportPayload): Promise<RecordTemplateImportResult> {
  return request<RecordTemplateImportResult>("/api/record-templates/import-preview", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function importRecordTemplates(payload: RecordTemplateImportPayload): Promise<RecordTemplateImportResult> {
  return request<RecordTemplateImportResult>("/api/record-templates/import", {
    method: "POST",
    body: JSON.stringify(payload)
  });
}

export function resolveFileUrl(fileUrl?: string | null): string {
  if (!fileUrl) {
    return "";
  }
  if (fileUrl.startsWith("http")) {
    return fileUrl;
  }
  return `${API_BASE_URL}${fileUrl}`;
}

export async function uploadEvidenceImage(
  projectId: number,
  payload: {
    section_code: string;
    file: File;
    caption?: string;
    alt_text?: string;
  }
): Promise<EvidenceImage> {
  const form = new FormData();
  form.append("section_code", payload.section_code);
  form.append("caption", payload.caption ?? "");
  form.append("alt_text", payload.alt_text ?? "");
  form.append("file", payload.file);

  const response = await fetch(`${API_BASE_URL}/api/projects/${projectId}/evidence`, {
    method: "POST",
    body: form
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `上传失败：${response.status}`);
  }

  return response.json() as Promise<EvidenceImage>;
}

export async function uploadEvidenceImages(
  projectId: number,
  payload: {
    section_code: string;
    files: File[];
    caption?: string;
    alt_text?: string;
  }
): Promise<EvidenceImage[]> {
  const form = new FormData();
  form.append("section_code", payload.section_code);
  form.append("caption", payload.caption ?? "");
  form.append("alt_text", payload.alt_text ?? "");
  payload.files.forEach((file) => form.append("files", file));

  const response = await fetch(`${API_BASE_URL}/api/projects/${projectId}/evidence/batch`, {
    method: "POST",
    body: form
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `上传失败：${response.status}`);
  }

  return response.json() as Promise<EvidenceImage[]>;
}

export function updateEvidenceImage(
  imageId: number,
  payload: Partial<Pick<EvidenceImage, "section_code" | "caption" | "alt_text" | "sort_order" | "display_width_in" | "display_height_in">>
): Promise<EvidenceImage> {
  return request<EvidenceImage>(`/api/evidence/${imageId}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export async function replaceEvidenceImageFile(imageId: number, file: File): Promise<EvidenceImage> {
  const form = new FormData();
  form.append("file", file);

  const response = await fetch(`${API_BASE_URL}/api/evidence/${imageId}/file`, {
    method: "POST",
    body: form
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `替换失败：${response.status}`);
  }

  return response.json() as Promise<EvidenceImage>;
}

export function deleteEvidenceImage(imageId: number): Promise<EvidenceImage> {
  return request<EvidenceImage>(`/api/evidence/${imageId}`, {
    method: "DELETE"
  });
}

export function reorderEvidenceImages(
  projectId: number,
  sectionCode: string,
  imageIds: number[]
): Promise<EvidenceImage[]> {
  return request<EvidenceImage[]>(`/api/projects/${projectId}/sections/${sectionCode}/evidence-order`, {
    method: "PUT",
    body: JSON.stringify({ image_ids: imageIds })
  });
}

export async function exportProjectDocx(projectId: number, mode: "editable" | "final"): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/api/projects/${projectId}/exports/docx?mode=${mode}`, {
    method: "POST"
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `导出失败：${response.status}`);
  }

  const blob = await response.blob();
  const fileName = _fileNameFromDisposition(response.headers.get("content-disposition")) ??
    `appendix_a_project_${projectId}_${mode}.docx`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return fileName;
}

export async function exportProjectXlsx(projectId: number): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/api/projects/${projectId}/exports/xlsx`, {
    method: "POST"
  });

  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, `导出打分表失败：${response.status}`));
  }

  const blob = await response.blob();
  const fileName = _fileNameFromDisposition(response.headers.get("content-disposition")) ??
    `score_workbook_project_${projectId}.xlsx`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  return fileName;
}

export function validateProject(projectId: number): Promise<ValidationResponse> {
  return request<ValidationResponse>(`/api/projects/${projectId}/validate`, {
    method: "POST"
  });
}

export function createRenderJob(projectId: number, mode: "editable" | "final" = "final"): Promise<RenderJob> {
  return request<RenderJob>(`/api/projects/${projectId}/render-jobs?mode=${mode}`, {
    method: "POST"
  });
}

export function getRenderJob(jobId: number): Promise<RenderJob> {
  return request<RenderJob>(`/api/render-jobs/${jobId}`);
}


export async function uploadDocxImport(file: File): Promise<DocxImportJob> {
  const form = new FormData();
  form.append("file", file);

  const response = await fetch(`${API_BASE_URL}/api/imports/docx`, {
    method: "POST",
    body: form
  });

  if (!response.ok) {
    const message = await responseErrorMessage(response, `导入失败：${response.status}`);
    throw new Error(message);
  }

  return response.json() as Promise<DocxImportJob>;
}

export function getDocxImport(jobId: number): Promise<DocxImportJob> {
  return request<DocxImportJob>(`/api/imports/${jobId}`);
}

export function createProjectFromDocxImport(jobId: number, projectName?: string): Promise<DocxImportJob> {
  return request<DocxImportJob>(`/api/imports/${jobId}/project`, {
    method: "POST",
    body: JSON.stringify({ project_name: projectName || null })
  });
}
async function responseErrorMessage(response: Response, fallback: string): Promise<string> {
  return responseErrorDetails(await response.text(), fallback).message;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly projectUuid?: string;
  readonly field?: string;
  readonly details?: unknown;

  constructor(
    message: string,
    options: {
      status: number;
      code?: string;
      projectUuid?: string;
      field?: string;
      details?: unknown;
    }
  ) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.projectUuid = options.projectUuid;
    this.field = options.field;
    this.details = options.details;
  }
}

async function responseApiError(response: Response, fallback: string): Promise<ApiError> {
  const parsed = responseErrorDetails(await response.text(), fallback);
  return new ApiError(parsed.message, {
    status: response.status,
    code: parsed.code,
    projectUuid: parsed.projectUuid,
    field: parsed.field,
    details: parsed.details
  });
}

function responseErrorDetails(text: string, fallback: string): {
  message: string;
  code?: string;
  projectUuid?: string;
  field?: string;
  details?: unknown;
} {
  if (!text) {
    return { message: fallback };
  }
  try {
    const payload = JSON.parse(text) as { detail?: unknown; message?: unknown; code?: unknown };
    const detail = payload.detail ?? (payload.code && typeof payload.message === "string" ? payload : payload.message);
    if (typeof detail === "string" && detail.trim()) {
      return { message: detail };
    }
    if (detail && typeof detail === "object") {
      const structured = detail as {
        code?: unknown;
        message?: unknown;
        project_uuid?: unknown;
        field?: unknown;
        details?: unknown;
        issues?: Array<{ section_code?: string; unit?: string; object_name?: string; field?: string; message?: string }>;
      };
      const message = typeof structured.message === "string" ? structured.message.trim() : "";
      const issueMessages = Array.isArray(structured.issues)
        ? structured.issues.slice(0, 5).map((issue) => {
            const location = [issue.section_code, issue.unit, issue.object_name].filter(Boolean).join(" / ");
            return `${location ? `${location}：` : ""}${issue.message ?? issue.field ?? "评分数据不完整"}`;
          })
        : [];
      if (message || issueMessages.length > 0) {
        const remainder = (structured.issues?.length ?? 0) - issueMessages.length;
        return {
          message: [message, ...issueMessages, remainder > 0 ? `另有 ${remainder} 项问题。` : ""].filter(Boolean).join("\n"),
          code: typeof structured.code === "string" ? structured.code : undefined,
          projectUuid: typeof structured.project_uuid === "string" ? structured.project_uuid : undefined,
          field: typeof structured.field === "string" ? structured.field : undefined,
          details: structured.details
        };
      }
    }
  } catch {
    // Plain-text error bodies can be shown directly.
  }
  return { message: text || fallback };
}
function _fileNameFromDisposition(disposition: string | null): string | null {
  if (!disposition) {
    return null;
  }
  const utf8Match = disposition.match(/filename\*=utf-8''([^;]+)/i);
  if (utf8Match) {
    return decodeURIComponent(utf8Match[1]);
  }
  const asciiMatch = disposition.match(/filename="?([^"]+)"?/i);
  return asciiMatch?.[1] ?? null;
}
