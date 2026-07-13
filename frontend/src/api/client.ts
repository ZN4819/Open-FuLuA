const rawExplicitApiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim();
const explicitApiBaseUrl = rawExplicitApiBaseUrl?.replace(/\/+$/, "") ?? "";
const API_BASE_URL = rawExplicitApiBaseUrl
  ? explicitApiBaseUrl
  : (import.meta.env.DEV ? "http://127.0.0.1:8000" : "");

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
  name: string;
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
  unit: string;
  object_name: string;
  subsystem: string;
  record_text: string;
  sort_order: number;
  metric_result: MetricResult;
};

export type AssessmentRowInput = {
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    ...init
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `请求失败：${response.status}`);
  }

  return response.json() as Promise<T>;
}

export function createProject(name: string): Promise<Project> {
  return request<Project>("/api/projects", {
    method: "POST",
    body: JSON.stringify({ name })
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
  const text = await response.text();
  if (!text) {
    return fallback;
  }
  try {
    const payload = JSON.parse(text) as { detail?: unknown; message?: unknown };
    const detail = payload.detail ?? payload.message;
    if (typeof detail === "string" && detail.trim()) {
      return detail;
    }
  } catch {
    // Plain-text error bodies can be shown directly.
  }
  return text || fallback;
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
