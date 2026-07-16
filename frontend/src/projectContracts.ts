export type ProjectType = "appendix_a" | "full_report";
export type WorkflowStatus = "draft" | "ready_for_review" | "confirmed";
export type ProjectCreationOperation = "create" | "migration_import" | "roundtrip_import" | "upgrade_copy";
export type ProjectWorkspaceView = "report_home" | "appendix_a";

export type ReportWorkspaceRoute =
  | { view: "overview" }
  | { view: "basics" }
  | { view: "objects" }
  | { view: "derived" }
  | { view: "migration_review" }
  | { view: "section"; sectionKey: string }
  | { view: "appendix_a"; sectionCode?: string };

export const FULL_REPORT_TEMPLATE_IDENTITY = Object.freeze({
  template_package_id: "report-2023-2025.12.08",
  template_edition: "2023",
  template_revision: "2025-12-08"
});

export type AppendixAProjectCreatePayload = {
  name: string;
};

export type FullReportProjectCreatePayload = AppendixAProjectCreatePayload & {
  project_type: "full_report";
  template_package_id: string;
  template_edition: string;
  template_revision: string;
};

export type ProjectUpgradeCopyPayload = {
  name: string;
  template_package_id: string;
  template_edition: string;
  template_revision: string;
  idempotency_key: string;
};

export function projectCreatePayload(
  name: string,
  projectType: ProjectType
): AppendixAProjectCreatePayload | FullReportProjectCreatePayload {
  if (projectType === "appendix_a") {
    // 保留旧客户端请求契约，不能为附录 A 请求追加新字段。
    return { name };
  }
  return {
    name,
    project_type: "full_report",
    ...FULL_REPORT_TEMPLATE_IDENTITY
  };
}

export function projectUpgradeCopyPayload(name: string, idempotencyKey: string): ProjectUpgradeCopyPayload {
  return {
    name,
    ...FULL_REPORT_TEMPLATE_IDENTITY,
    idempotency_key: idempotencyKey
  };
}

export function projectTypeLabel(projectType: ProjectType): string {
  return projectType === "full_report" ? "完整报告" : "仅附录 A";
}

export function workflowStatusLabel(status: WorkflowStatus): string {
  switch (status) {
    case "ready_for_review":
      return "待复核";
    case "confirmed":
      return "已确认";
    default:
      return "草稿";
  }
}

export function defaultProjectWorkspace(projectType: ProjectType): ProjectWorkspaceView {
  return projectType === "full_report" ? "report_home" : "appendix_a";
}

export function canUpgradeProject(projectType: ProjectType): boolean {
  return projectType === "appendix_a";
}

export function projectWorkspacePath(projectUuid: string, route: ReportWorkspaceRoute): string {
  const root = `/projects/${encodeURIComponent(projectUuid)}`;
  if (route.view === "overview") {
    return `${root}/overview`;
  }
  if (route.view === "basics") {
    return `${root}/basics`;
  }
  if (route.view === "objects") {
    return `${root}/objects`;
  }
  if (route.view === "derived") {
    return `${root}/derived`;
  }
  if (route.view === "migration_review") {
    return `${root}/migration-review`;
  }
  if (route.view === "appendix_a") {
    return route.sectionCode
      ? `${root}/appendix-a/${encodeURIComponent(route.sectionCode)}`
      : `${root}/appendix-a`;
  }
  return `${root}/report/${encodeURIComponent(route.sectionKey)}`;
}

export function parseProjectWorkspacePath(pathname: string): {
  projectUuid: string;
  route: ReportWorkspaceRoute;
} | null {
  const segments = pathname.split("/").filter(Boolean).map((segment) => {
    try {
      return decodeURIComponent(segment);
    } catch {
      return segment;
    }
  });
  if (segments.length < 3 || segments[0] !== "projects" || !segments[1]) {
    return null;
  }

  const projectUuid = segments[1];
  if (segments[2] === "overview" && segments.length === 3) {
    return { projectUuid, route: { view: "overview" } };
  }
  if (segments[2] === "basics" && segments.length === 3) {
    return { projectUuid, route: { view: "basics" } };
  }
  if (segments[2] === "objects" && segments.length === 3) {
    return { projectUuid, route: { view: "objects" } };
  }
  if (segments[2] === "derived" && segments.length === 3) {
    return { projectUuid, route: { view: "derived" } };
  }
  if (segments[2] === "migration-review" && segments.length === 3) {
    return { projectUuid, route: { view: "migration_review" } };
  }
  if (segments[2] === "report" && segments[3] && segments.length === 4) {
    return { projectUuid, route: { view: "section", sectionKey: segments[3] } };
  }
  if (segments[2] === "appendix-a" && segments.length <= 4) {
    return {
      projectUuid,
      route: { view: "appendix_a", sectionCode: segments[3] || undefined }
    };
  }
  return null;
}

export function reportImportPath(jobId: number): string {
  if (!Number.isSafeInteger(jobId) || jobId <= 0) {
    throw new Error("迁移任务 ID 必须是正整数");
  }
  return `/report-imports/${jobId}`;
}

export function parseReportImportPath(pathname: string): { jobId: number } | null {
  const segments = pathname.split("/").filter(Boolean);
  if (segments.length !== 2 || segments[0] !== "report-imports" || !/^\d+$/.test(segments[1])) {
    return null;
  }
  const jobId = Number(segments[1]);
  return Number.isSafeInteger(jobId) && jobId > 0 ? { jobId } : null;
}
