const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

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
  object_score?: string | null;
  unit_score?: string | null;
  compliance?: string | null;
};

export type AssessmentRow = {
  id: number;
  section_id: number;
  unit: string;
  object_name: string;
  record_text: string;
  sort_order: number;
  metric_result: MetricResult;
};

export type AssessmentRowInput = {
  unit: string;
  object_name: string;
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

export type RecordTemplate = {
  id: string;
  section_code: string;
  table_type: "technical" | "management";
  unit: string;
  object_name: string;
  title: string;
  record_text: string;
  source_row: number;
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

export function getSectionDetail(projectId: number, code: string): Promise<SectionDetail> {
  return request<SectionDetail>(`/api/projects/${projectId}/sections/${code}`);
}

export function updateSectionDetail(
  projectId: number,
  code: string,
  payload: {
    title?: string | null;
    table_title?: string | null;
    rows: AssessmentRowInput[];
  }
): Promise<SectionDetail> {
  return request<SectionDetail>(`/api/projects/${projectId}/sections/${code}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
}

export function getTemplateProfile(): Promise<TemplateProfile> {
  return request<TemplateProfile>("/api/template-profile");
}

export function getRecordTemplates(sectionCode?: string): Promise<RecordTemplate[]> {
  const query = sectionCode ? `?section_code=${encodeURIComponent(sectionCode)}` : "";
  return request<RecordTemplate[]>(`/api/record-templates${query}`);
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

export function updateEvidenceImage(
  imageId: number,
  payload: Partial<Pick<EvidenceImage, "section_code" | "caption" | "alt_text" | "sort_order" | "display_width_in" | "display_height_in">>
): Promise<EvidenceImage> {
  return request<EvidenceImage>(`/api/evidence/${imageId}`, {
    method: "PUT",
    body: JSON.stringify(payload)
  });
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
