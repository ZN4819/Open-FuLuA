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
