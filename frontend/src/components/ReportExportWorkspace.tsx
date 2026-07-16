import { useEffect, useRef, useState } from "react";

import {
  createReportExportJob,
  downloadReportExportDocx,
  getReportExportIssues,
  getReportExportJob,
  validateReportExport,
  type ReportExportIssue,
  type ReportExportIssueCollection,
  type ReportExportJob,
  type ReportExportMode,
  type ReportExportValidation
} from "../api/reportClient.ts";

type ReportExportWorkspaceProps = {
  projectUuid: string;
  projectRevision?: number;
  hasUnsavedChanges: boolean;
};

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "cancelled"]);
const VERSION_PATTERN = /^V[0-9]+(?:\.[0-9]+){1,3}$/i;

export function ReportExportWorkspace({
  projectUuid,
  projectRevision,
  hasUnsavedChanges
}: ReportExportWorkspaceProps) {
  const [version, setVersion] = useState("V1.0");
  const [validation, setValidation] = useState<ReportExportValidation>();
  const [job, setJob] = useState<ReportExportJob>();
  const [jobIssues, setJobIssues] = useState<ReportExportIssueCollection>();
  const [isWorking, setIsWorking] = useState(false);
  const [message, setMessage] = useState<string>();
  const [error, setError] = useState<string>();
  const operationRef = useRef(0);

  useEffect(() => () => { operationRef.current += 1; }, []);

  async function handleExport(mode: ReportExportMode) {
    if (!projectRevision || hasUnsavedChanges || isWorking) return;
    const normalizedVersion = version.trim().toUpperCase();
    setError(undefined);
    setMessage(undefined);
    setValidation(undefined);
    setJobIssues(undefined);
    setJob(undefined);
    if (!VERSION_PATTERN.test(normalizedVersion)) {
      setError("版本号应使用 V1.0 形式。");
      return;
    }

    const operation = ++operationRef.current;
    setIsWorking(true);
    try {
      const checked = await validateReportExport(projectUuid, mode);
      if (operation !== operationRef.current) return;
      setValidation(checked);
      if (checked.project_revision !== projectRevision) {
        throw new Error("项目数据已变化，请刷新正文生成工作台后再导出。");
      }
      if (mode === "final" && !checked.valid) {
        setError("正式版校验未通过，请先处理下方错误。警告不会单独阻断导出。");
        return;
      }

      const created = await createReportExportJob(projectUuid, {
        mode,
        version: normalizedVersion,
        expected_project_revision: checked.project_revision
      });
      if (operation !== operationRef.current) return;
      setVersion(normalizedVersion);
      setJob(created);
      setMessage(mode === "final"
        ? "正式版正在由 Microsoft Word 刷新目录、页码和交叉引用。"
        : "草稿正在装配；若本机 Word 不可用，将保留未刷新字段的草稿。"
      );

      const completed = await pollExportJob(created, operation, operationRef);
      if (!completed || operation !== operationRef.current) return;
      setJob(completed);
      const issues = await getReportExportIssues(completed.job_uuid);
      if (operation !== operationRef.current) return;
      setJobIssues(issues);
      if (completed.status !== "succeeded") {
        setError(completed.error_message || "完整报告导出失败，请查看下方错误。" );
        return;
      }
      const fileName = await downloadReportExportDocx(completed.job_uuid);
      if (operation !== operationRef.current) return;
      setMessage(`已生成并下载：${fileName}`);
    } catch (exportError) {
      if (operation === operationRef.current) {
        setError(errorMessage(exportError, "完整报告导出失败"));
      }
    } finally {
      if (operation === operationRef.current) setIsWorking(false);
    }
  }

  async function handleDownload() {
    if (!job?.download_available) return;
    setError(undefined);
    try {
      const fileName = await downloadReportExportDocx(job.job_uuid);
      setMessage(`已重新下载：${fileName}`);
    } catch (downloadError) {
      setError(errorMessage(downloadError, "下载完整报告失败"));
    }
  }

  const validationErrors = validation?.issues.filter((item) => item.severity === "error") ?? [];
  const validationWarnings = validation?.issues.filter((item) => item.severity === "warning") ?? [];
  const errors = jobIssues?.errors ?? validationErrors;
  const warnings = jobIssues?.warnings ?? validationWarnings;
  const blocked = !projectRevision || hasUnsavedChanges || isWorking;

  return (
    <section className="report-form-card report-export-card" aria-label="完整报告导出">
      <div className="report-card-heading">
        <div><p className="eyebrow">R4 完整报告装配</p><h4>生成 DOCX</h4></div>
        <span className={`derived-status ${job?.status ?? "not_generated"}`}>{exportStatusLabel(job?.status)}</span>
      </div>
      <p>导出基于当前 revision 的不可变快照。正式版必须通过完整校验，并由 Microsoft Word 原生刷新目录、页码及交叉引用。</p>
      <div className="report-export-controls">
        <label>
          <span>报告版本</span>
          <input
            value={version}
            onChange={(event) => setVersion(event.target.value)}
            disabled={isWorking}
            placeholder="V1.0"
            aria-label="报告版本"
          />
        </label>
        <div className="form-actions">
          <button type="button" className="secondary-button" onClick={() => void handleExport("draft")} disabled={blocked}>
            {isWorking && job?.mode === "draft" ? "正在生成草稿..." : "生成草稿"}
          </button>
          <button type="button" onClick={() => void handleExport("final")} disabled={blocked}>
            {isWorking && job?.mode === "final" ? "正在生成正式版..." : "生成正式版"}
          </button>
          {job?.download_available ? (
            <button type="button" className="secondary-button" onClick={() => void handleDownload()}>重新下载 DOCX</button>
          ) : null}
        </div>
      </div>
      {hasUnsavedChanges ? <p className="warning-text">当前有未保存的风险或正文内容，请保存后再导出。</p> : null}
      {!projectRevision ? <p className="warning-text">尚未生成可装配的正文 revision。</p> : null}
      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="success-message" role="status">{message}</p> : null}

      {job ? (
        <dl className="report-export-facts">
          <div><dt>项目 revision</dt><dd>{job.project_revision}</dd></div>
          <div><dt>母版包</dt><dd>{job.template_package_id}</dd></div>
          <div><dt>Word 刷新</dt><dd>{wordStatusLabel(job.word_refresh_status)}</dd></div>
          <div><dt>页数</dt><dd>{job.page_count ?? "—"}</dd></div>
          <div><dt>装配上下文</dt><dd>{shortHash(job.assembly_context_hash)}</dd></div>
          <div><dt>输出摘要</dt><dd>{shortHash(job.docx_hash)}</dd></div>
        </dl>
      ) : null}
      {errors.length ? <ReportExportIssueList title={`错误（${errors.length}）`} issues={errors} tone="error" /> : null}
      {warnings.length ? <ReportExportIssueList title={`警告（${warnings.length}）`} issues={warnings} tone="warning" /> : null}
    </section>
  );
}

async function pollExportJob(
  initial: ReportExportJob,
  operation: number,
  operationRef: { current: number }
): Promise<ReportExportJob | undefined> {
  let current = initial;
  for (let attempt = 0; attempt < 600; attempt += 1) {
    if (TERMINAL_STATUSES.has(current.status)) return current;
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
    if (operation !== operationRef.current) return undefined;
    current = await getReportExportJob(current.job_uuid);
  }
  throw new Error("导出任务等待超时，请稍后刷新后重试。");
}

function ReportExportIssueList({
  title,
  issues,
  tone
}: {
  title: string;
  issues: ReportExportIssue[];
  tone: "error" | "warning";
}) {
  return (
    <div className={`report-export-issues ${tone}`}>
      <strong>{title}</strong>
      <ul>
        {issues.map((issue, index) => (
          <li key={`${issue.code}:${index}`}>
            <span>{issue.code}</span>
            <p>{issue.message}<small>{issueLocation(issue)}</small></p>
          </li>
        ))}
      </ul>
    </div>
  );
}

function issueLocation(issue: ReportExportIssue): string {
  return [issue.section_code, issue.indicator, issue.object_name, issue.field, issue.block_id]
    .filter(Boolean)
    .join(" / ");
}

function exportStatusLabel(status?: ReportExportJob["status"]): string {
  if (!status) return "未生成";
  return ({ queued: "等待中", running: "装配中", succeeded: "已完成", failed: "失败", cancelled: "已取消" } as const)[status];
}

function wordStatusLabel(status: ReportExportJob["word_refresh_status"]): string {
  return ({ not_started: "未开始", skipped: "草稿跳过", succeeded: "已完成", failed: "失败" } as const)[status];
}

function shortHash(value?: string | null): string {
  return value ? `${value.slice(0, 12)}…` : "—";
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
