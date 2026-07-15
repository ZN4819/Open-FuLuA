export type ProjectType = "appendix_a" | "full_report";
export type WorkflowStatus = "draft" | "ready_for_review" | "confirmed";
export type ProjectCreationOperation = "create" | "migration_import" | "roundtrip_import" | "upgrade_copy";
export type ProjectWorkspaceView = "report_home" | "appendix_a";

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
