import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";

import { ApiError } from "../api/client.ts";
import {
  confirmReportImport,
  getReportImport,
  updateReportImportResolutions,
  uploadReportImport,
  type ReportImportAppendixASource,
  type ReportImportIssue,
  type ReportImportJob,
  type ReportImportResolutionAction,
  type ReportImportResolutionInput
} from "../api/reportImportClient.ts";
import { getProjectReportMigrationReview } from "../api/reportClient.ts";

type ReportMigrationWorkspaceProps = {
  initialJobId?: number;
  onCreated: (job: ReportImportJob) => void | Promise<void>;
  onJobChanged?: (jobId?: number) => void;
  onPendingChange?: (pending: boolean) => void;
};

type AppendixSourceCandidate = {
  project_uuid: string;
  name: string;
  updated_at?: string | null;
  sections_present: string[];
  validation_error_count: number;
  complete: boolean;
};

type ResolutionDraft = {
  action: ReportImportResolutionAction;
  resolvedValue?: unknown;
  valueMode?: "candidate" | "manual";
};

type ResolutionScalar = string | number;

type ResolutionCandidateOption = {
  label: string;
  value: ResolutionScalar;
};

const ACTIVE_JOB_STATUSES = new Set(["uploaded", "parsing"]);

export function ReportMigrationWorkspace({ initialJobId, onCreated, onJobChanged, onPendingChange }: ReportMigrationWorkspaceProps) {
  const [file, setFile] = useState<File | null>(null);
  const [job, setJob] = useState<ReportImportJob>();
  const [projectName, setProjectName] = useState("");
  const [appendixSource, setAppendixSource] = useState<ReportImportAppendixASource>("document");
  const [appendixProjectUuid, setAppendixProjectUuid] = useState("");
  const [resolutionDrafts, setResolutionDrafts] = useState<Record<number, ResolutionDraft>>({});
  const [resolutionsDirty, setResolutionsDirty] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isSavingResolutions, setIsSavingResolutions] = useState(false);
  const [isConfirming, setIsConfirming] = useState(false);
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();

  const issueGroups = useMemo(() => groupIssues(job?.issues ?? []), [job?.issues]);
  const appendixCandidates = useMemo(
    () => appendixCandidatesFromSummary(job?.summary.appendix_sources),
    [job?.summary.appendix_sources]
  );
  const completeAppendixCandidates = appendixCandidates.filter((candidate) => candidate.complete);
  const selectedAppendixCandidateIsComplete = completeAppendixCandidates.some(
    (candidate) => candidate.project_uuid === appendixProjectUuid
  );
  const hasPendingReview = Boolean(job && job.status !== "succeeded" && job.status !== "failed");

  useEffect(() => {
    onPendingChange?.(hasPendingReview);
    return () => onPendingChange?.(false);
  }, [hasPendingReview, onPendingChange]);

  useEffect(() => {
    if (!hasPendingReview) return;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [hasPendingReview]);

  useEffect(() => {
    if (!initialJobId) {
      setJob((current) => current && window.location.pathname === "/" ? undefined : current);
      return;
    }
    if (job?.id === initialJobId) return;
    setError(undefined);
    setMessage("正在恢复迁移预览...");
    void getReportImport(initialJobId)
      .then((restoredJob) => {
        applyJob(restoredJob);
        setProjectName((current) => current || suggestProjectName(restoredJob.original_name));
        setMessage("已恢复迁移预览和已保存的歧义处理。");
      })
      .catch((loadError) => {
        setMessage(undefined);
        setError(errorText(loadError, "恢复迁移任务失败"));
      });
  }, [initialJobId]);

  useEffect(() => {
    if (!job || !ACTIVE_JOB_STATUSES.has(job.status)) {
      return;
    }
    const timer = window.setTimeout(() => {
      void getReportImport(job.id)
        .then((nextJob) => applyJob(nextJob, false))
        .catch((pollError) => setError(errorText(pollError, "读取迁移任务进度失败")));
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [job?.id, job?.status, job?.job_revision]);

  function applyJob(nextJob: ReportImportJob, resetChoices = true) {
    setJob(nextJob);
    if (resetChoices) {
      setResolutionDrafts(buildResolutionDrafts(nextJob));
      setResolutionsDirty(false);
    }
    if (nextJob.appendix_a_source) {
      setAppendixSource(nextJob.appendix_a_source);
    }
  }

  async function handleUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setError("请选择当前三级模板族的 DOCX 报告。");
      return;
    }
    setIsUploading(true);
    setError(undefined);
    setMessage(undefined);
    try {
      const nextJob = await uploadReportImport(file, "migration");
      applyJob(nextJob);
      onJobChanged?.(nextJob.id);
      setProjectName(suggestProjectName(file.name));
      setMessage(ACTIVE_JOB_STATUSES.has(nextJob.status) ? "文件已安全复制，正在解析。" : "迁移预览已生成，请逐项审阅后再创建项目。");
    } catch (uploadError) {
      setError(errorText(uploadError, "完整报告迁移解析失败"));
    } finally {
      setIsUploading(false);
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const nextFile = event.currentTarget.files?.[0] ?? null;
    if (hasPendingReview && !window.confirm("当前迁移预览尚未完成，确定放弃并选择其他文件吗？")) {
      event.currentTarget.value = "";
      return;
    }
    setFile(nextFile);
    setJob(undefined);
    onJobChanged?.(undefined);
    setResolutionDrafts({});
    setResolutionsDirty(false);
    setError(undefined);
    setMessage(undefined);
  }

  function changeResolution(issue: ReportImportIssue, action: ReportImportResolutionAction) {
    setResolutionDrafts((current) => ({
      ...current,
      [issue.id]: {
        action,
        resolvedValue: action === "adopt_candidate" && isResolutionScalar(issue.candidate_value)
          ? issue.candidate_value
          : undefined,
        valueMode: action === "adopt_candidate" && isResolutionScalar(issue.candidate_value)
          ? "candidate"
          : undefined
      }
    }));
    setResolutionsDirty(true);
    setError(undefined);
    setMessage(undefined);
  }

  function changeResolutionValue(
    issue: ReportImportIssue,
    resolvedValue: ResolutionScalar | undefined,
    valueMode: ResolutionDraft["valueMode"]
  ) {
    setResolutionDrafts((current) => ({
      ...current,
      [issue.id]: {
        action: "adopt_candidate",
        resolvedValue,
        valueMode
      }
    }));
    setResolutionsDirty(true);
    setError(undefined);
    setMessage(undefined);
  }

  async function handleSaveResolutions() {
    if (!job) return;
    const resolvableIssues = job.issues.filter(isResolvableIssue);
    const invalidAdoption = resolvableIssues.find((issue) => {
      const draft = resolutionDrafts[issue.id];
      return draft?.action === "adopt_candidate" && !isResolutionScalar(draft.resolvedValue);
    });
    if (invalidAdoption) {
      setError(`“${invalidAdoption.field_path || invalidAdoption.code}”采用候选时，必须选择一个标量候选或人工填写标量值。`);
      return;
    }
    const payload: ReportImportResolutionInput[] = resolvableIssues
      .map((issue) => {
        const draft = resolutionDrafts[issue.id] ?? { action: "keep_original" as const };
        return {
          issue_id: issue.id,
          revision: issue.revision,
          action: draft.action,
          ...(draft.action === "adopt_candidate" ? { resolved_value: draft.resolvedValue } : {})
        };
      });
    setIsSavingResolutions(true);
    setError(undefined);
    try {
      const nextJob = await updateReportImportResolutions(job.id, job.job_revision, payload);
      applyJob(nextJob);
      setMessage("歧义处理已保存，确认状态已按后端规则重新计算。");
    } catch (saveError) {
      setError(errorText(saveError, "保存歧义处理失败，请重新读取任务后再试"));
    } finally {
      setIsSavingResolutions(false);
    }
  }

  async function handleConfirm() {
    if (!job || !projectName.trim()) return;
    if (appendixSource === "existing_project" && !selectedAppendixCandidateIsComplete) {
      setError("请选择后端当前确认完整、可复制的附录 A 项目。");
      return;
    }
    if (resolutionsDirty) {
      setError("歧义处理尚未保存，请先保存处理结果。");
      return;
    }
    setIsConfirming(true);
    setError(undefined);
    try {
      const resolvableIssueIds = new Set(job.issues.filter(isResolvableIssue).map((issue) => issue.id));
      const confirmed = await confirmReportImport(job.id, {
        job_revision: job.job_revision,
        project_name: projectName.trim(),
        appendix_a_source: appendixSource,
        ...(appendixSource === "existing_project" ? { appendix_a_project_uuid: appendixProjectUuid } : {}),
        accepted_resolutions: job.resolutions
          .filter((resolution) => resolution.action === "adopt_candidate" && resolvableIssueIds.has(resolution.issue_id))
          .map((resolution) => resolution.id),
        keep_unresolved_original: true
      });
      applyJob(confirmed);
      if (!confirmed.created_project_uuid) {
        throw new Error("后端未返回新项目标识，未跳转到项目。");
      }
      setMessage("迁移项目已创建，正在打开迁移审阅页。");
      await onCreated(confirmed);
    } catch (confirmError) {
      setError(errorText(confirmError, "确认迁移失败"));
    } finally {
      setIsConfirming(false);
    }
  }

  const isProcessing = Boolean(job && ACTIVE_JOB_STATUSES.has(job.status));
  const canConfirm = Boolean(
    job?.confirmable &&
    !isProcessing &&
    !resolutionsDirty &&
    projectName.trim() &&
    (appendixSource === "document" || selectedAppendixCandidateIsComplete)
  );

  return (
    <section className="report-migration-panel" aria-labelledby="report-migration-title">
      <div className="report-migration-heading">
        <div>
          <p className="eyebrow">一次性迁移</p>
          <h3 id="report-migration-title">迁移既有完整报告</h3>
          <p>仅支持当前三级 2023 / 2025-12-08 模板族。系统创建新草稿项目，不修改或回写源 DOCX。</p>
        </div>
        <span className="template-version-badge">非往返编辑</span>
      </div>

      <form className="report-migration-upload" onSubmit={handleUpload}>
        <label htmlFor="fullReportMigrationFile">
          <span>完整报告 DOCX</span>
          <input
            id="fullReportMigrationFile"
            type="file"
            accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={handleFileChange}
          />
        </label>
        <button type="submit" disabled={!file || isUploading}>
          {isUploading ? "安全扫描与解析中..." : "上传并生成迁移预览"}
        </button>
      </form>

      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="success" aria-live="polite">{message}</p> : null}
      {job ? (
        <div className="report-migration-preview">
          <MigrationStatus job={job} />
          {!isProcessing && job.status !== "failed" ? (
            <>
              <section className="report-migration-zone" aria-labelledby="migration-template-match">
                <MigrationZoneHeading index="1" id="migration-template-match" title="模板匹配" count={job.fingerprint.matched ? "已匹配" : "未匹配"} />
                <dl className="report-migration-fingerprint">
                  <div><dt>模板版本</dt><dd>{job.detected_edition ?? "未识别"} / {job.detected_revision ?? "未识别"}</dd></div>
                  <div><dt>顶层表格</dt><dd>{job.fingerprint.table_count}</dd></div>
                  <div><dt>分节</dt><dd>{job.fingerprint.section_count}</dd></div>
                  <div><dt>指纹</dt><dd title={job.fingerprint.sha256}>{shortHash(job.fingerprint.sha256)}</dd></div>
                </dl>
              </section>

              <section className="report-migration-zone" aria-labelledby="migration-chapter-stats">
                <MigrationZoneHeading index="2" id="migration-chapter-stats" title="章节统计" count={summaryCount(job.summary.chapter_stats)} />
                <SummaryValue value={job.summary.chapter_stats} emptyText="未返回章节统计。" />
              </section>

              <section className="report-migration-zone" aria-labelledby="migration-auto-mappings">
                <MigrationZoneHeading index="3" id="migration-auto-mappings" title="自动映射" count={issueGroups.automatic.length || summaryCount(job.summary.automatic_mappings)} />
                {issueGroups.automatic.length
                  ? <IssueCards issues={issueGroups.automatic} readonly />
                  : <SummaryValue value={job.summary.automatic_mappings} emptyText="没有自动映射项。" />}
              </section>

              <section className="report-migration-zone" aria-labelledby="migration-pending">
                <MigrationZoneHeading index="4" id="migration-pending" title="待确认与歧义处理" count={issueGroups.pending.length} />
                <IssueCards
                  issues={issueGroups.pending}
                  drafts={resolutionDrafts}
                  onActionChange={changeResolution}
                  onResolvedValueChange={changeResolutionValue}
                />
                {issueGroups.pending.some(isResolvableIssue) ? (
                  <div className="report-migration-zone-actions">
                    <button type="button" onClick={() => void handleSaveResolutions()} disabled={!resolutionsDirty || isSavingResolutions}>
                      {isSavingResolutions ? "保存中..." : resolutionsDirty ? "保存歧义处理" : "歧义处理已保存"}
                    </button>
                  </div>
                ) : null}
              </section>

              <section className="report-migration-zone" aria-labelledby="migration-unmapped">
                <MigrationZoneHeading index="5" id="migration-unmapped" title="未识别内容" count={issueGroups.unmapped.length} />
                <p className="report-form-help">未识别原文会进入新项目审阅队列，不会静默丢弃或猜测写入。</p>
                <IssueCards issues={issueGroups.unmapped} readonly />
              </section>

              <section className="report-migration-zone" aria-labelledby="migration-appendix-source">
                <MigrationZoneHeading index="6" id="migration-appendix-source" title="附录来源与创建确认" />
                <fieldset className="report-migration-source-options">
                  <legend>附录 A 来源</legend>
                  <label>
                    <input type="radio" checked={appendixSource === "document"} onChange={() => setAppendixSource("document")} />
                    <span><strong>当前 DOCX</strong><small>仅提取 Word 可表达的原始事实，评分由后端重算。</small></span>
                  </label>
                  <label>
                    <input type="radio" checked={appendixSource === "existing_project"} onChange={() => setAppendixSource("existing_project")} />
                    <span><strong>从已有附录 A 项目复制</strong><small>复制 A-1 至 A-8、图片与内部评分参数。</small></span>
                  </label>
                </fieldset>
                {appendixSource === "existing_project" ? (
                  <label className="report-migration-field">
                    <span>附录 A 项目</span>
                    <select value={appendixProjectUuid} onChange={(event) => setAppendixProjectUuid(event.target.value)} required>
                      <option value="">请选择项目</option>
                      {completeAppendixCandidates.map((candidate) => (
                        <option key={candidate.project_uuid} value={candidate.project_uuid}>
                          {candidate.name} · 更新于 {candidate.updated_at ? formatDate(candidate.updated_at) : "未知"}
                        </option>
                      ))}
                    </select>
                    {!appendixCandidates.length ? <small>后端未返回可核验的附录 A 候选，不能假定已有项目完整。</small> : null}
                    {appendixCandidates.length && !completeAppendixCandidates.length ? <small>候选均未通过完整度校验，暂不可复制。</small> : null}
                  </label>
                ) : null}
                {appendixSource === "existing_project" && appendixCandidates.length ? (
                  <ul className="report-migration-appendix-candidates" aria-label="附录 A 候选完整度">
                    {appendixCandidates.map((candidate) => (
                      <li className={candidate.complete ? "complete" : "incomplete"} key={candidate.project_uuid}>
                        <strong>{candidate.name}</strong>
                        <span>{candidate.sections_present.length}/8 章节 · {candidate.validation_error_count} 个校验错误</span>
                        <em>{candidate.complete ? "可复制" : "不可复制"}</em>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <label className="report-migration-field">
                  <span>新项目名称</span>
                  <input value={projectName} onChange={(event) => setProjectName(event.target.value)} maxLength={120} required />
                </label>
                <div className="report-migration-confirm">
                  <p>
                    {job.confirmable
                      ? "后端确认闸门已通过；创建后状态固定为草稿，并进入迁移审阅页。"
                      : "当前任务仍有阻断项，请先处理错误或歧义。"}
                  </p>
                  <button type="button" onClick={() => void handleConfirm()} disabled={!canConfirm || isConfirming}>
                    {isConfirming ? "创建中..." : "确认并创建新草稿项目"}
                  </button>
                </div>
              </section>
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export function ReportMigrationReview({ projectUuid }: { projectUuid: string }) {
  const [job, setJob] = useState<ReportImportJob>();
  const [resolutionDrafts, setResolutionDrafts] = useState<Record<number, ResolutionDraft>>({});
  const [resolutionsDirty, setResolutionsDirty] = useState(false);
  const [isSavingResolutions, setIsSavingResolutions] = useState(false);
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    setError(undefined);
    setMessage(undefined);
    setNotFound(false);
    getProjectReportMigrationReview(projectUuid)
      .then((nextJob) => {
        setJob(nextJob);
        setResolutionDrafts({});
        setResolutionsDirty(false);
      })
      .catch((loadError) => {
        if (loadError instanceof ApiError && loadError.status === 404) {
          setNotFound(true);
          return;
        }
        setError(errorText(loadError, "读取迁移审阅记录失败"));
      });
  }, [projectUuid]);

  const editableIssues = job?.issues.filter((issue) => isPostCreateReviewIssue(job, issue)) ?? [];

  function changeReviewResolution(issue: ReportImportIssue, action: ReportImportResolutionAction) {
    setResolutionDrafts((current) => ({
      ...current,
      [issue.id]: {
        action,
        resolvedValue: action === "adopt_candidate" && isResolutionScalar(issue.candidate_value)
          ? issue.candidate_value
          : undefined,
        valueMode: action === "adopt_candidate" && isResolutionScalar(issue.candidate_value)
          ? "candidate"
          : undefined
      }
    }));
    setResolutionsDirty(true);
    setError(undefined);
    setMessage(undefined);
  }

  function changeReviewResolutionValue(
    issue: ReportImportIssue,
    resolvedValue: ResolutionScalar | undefined,
    valueMode: ResolutionDraft["valueMode"]
  ) {
    setResolutionDrafts((current) => ({
      ...current,
      [issue.id]: { action: "adopt_candidate", resolvedValue, valueMode }
    }));
    setResolutionsDirty(true);
    setError(undefined);
    setMessage(undefined);
  }

  async function handleSaveReviewResolutions() {
    if (!job) return;
    const selectedIssues = editableIssues.filter((issue) => {
      const action = resolutionDrafts[issue.id]?.action;
      return action === "adopt_candidate" || action === "skip";
    });
    const invalidAdoption = selectedIssues.find((issue) => {
      const draft = resolutionDrafts[issue.id];
      return draft.action === "adopt_candidate" && !isResolutionScalar(draft.resolvedValue);
    });
    if (invalidAdoption) {
      setError(`“${invalidAdoption.field_path || invalidAdoption.code}”采用候选时，必须选择一个标量候选或人工填写标量值。`);
      return;
    }
    if (!selectedIssues.length) {
      setError("请先为至少一个待处理项选择“采用候选”或“明确跳过”。");
      return;
    }
    const payload: ReportImportResolutionInput[] = selectedIssues.map((issue) => {
      const draft = resolutionDrafts[issue.id];
      return {
        issue_id: issue.id,
        revision: issue.revision,
        action: draft.action,
        ...(draft.action === "adopt_candidate" ? { resolved_value: draft.resolvedValue } : {})
      };
    });
    setIsSavingResolutions(true);
    setError(undefined);
    try {
      const nextJob = await updateReportImportResolutions(
        job.id,
        job.job_revision,
        payload,
        job.created_project_updated_at
      );
      setJob(nextJob);
      setResolutionDrafts({});
      setResolutionsDirty(false);
      setMessage("迁移审阅处理已保存，正式导出阻断状态已重新计算。");
    } catch (saveError) {
      setError(errorText(saveError, "保存迁移审阅处理失败，请重新读取任务后再试"));
    } finally {
      setIsSavingResolutions(false);
    }
  }

  if (notFound) return <div className="report-page-stack"><section className="report-page-heading"><p className="eyebrow">迁移审阅</p><h3>没有迁移记录</h3><p>该项目不是通过既有完整报告迁移创建，或关联任务已按保留策略清理。</p></section></div>;
  if (error) return <div className="report-workbench-failure" role="alert"><p>{error}</p></div>;
  if (!job) return <p className="report-loading">正在读取迁移审阅记录...</p>;

  const groups = groupIssues(job.issues);
  return (
    <div className="report-page-stack report-migration-review">
      <section className="report-page-heading">
        <p className="eyebrow">迁移审阅</p>
        <h3>来源、差异与待办</h3>
        <p>此页保留迁移来源和处理结果。未解决项及“阻断最终导出”问题仍需在正式交付前处理。</p>
      </section>
      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="success" aria-live="polite">{message}</p> : null}
      <MigrationStatus job={job} />
      <div className="report-metric-grid">
        <ReportReviewMetric label="自动映射" value={groups.automatic.length} />
        <ReportReviewMetric label="待确认" value={groups.pending.length} />
        <ReportReviewMetric label="未识别" value={groups.unmapped.length} />
        <ReportReviewMetric label="阻断最终导出" value={job.issues.filter((issue) => issue.blocks_final_export).length} />
      </div>
      <section className="report-migration-zone">
        <MigrationZoneHeading index="1" id="migration-review-resolutions" title="已保存处理结果" count={job.resolutions.length} />
        {job.resolutions.length ? (
          <ul className="report-migration-resolution-list">
            {job.resolutions.map((resolution) => (
              <li key={resolution.id}><strong>{resolution.field_path ?? `问题 ${resolution.issue_id}`}</strong><span>{resolutionActionLabel(resolution.action)}</span></li>
            ))}
          </ul>
        ) : <p className="report-migration-empty">本次迁移没有已保存的人工处理。</p>}
      </section>
      <section className="report-migration-zone">
        <MigrationZoneHeading index="2" id="migration-review-issues" title="迁移问题与导出待办" count={job.issues.length} />
        <p className="report-form-help">仍为“保留原文待确认”的项目，可在此采用单个标量候选或明确跳过；已采用、已跳过及硬阻断项保持只读。</p>
        <IssueCards
          issues={job.issues}
          drafts={resolutionDrafts}
          resolutionActions={["adopt_candidate", "skip"]}
          isIssueEditable={(issue) => isPostCreateReviewIssue(job, issue)}
          onActionChange={changeReviewResolution}
          onResolvedValueChange={changeReviewResolutionValue}
        />
        {editableIssues.length ? (
          <div className="report-migration-zone-actions">
            <button type="button" onClick={() => void handleSaveReviewResolutions()} disabled={!resolutionsDirty || isSavingResolutions}>
              {isSavingResolutions ? "保存中..." : resolutionsDirty ? "保存最终处理" : "请选择待处理项"}
            </button>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function MigrationStatus({ job }: { job: ReportImportJob }) {
  const errorCount = job.issues.filter((issue) => issue.severity === "error").length;
  const warningCount = job.issues.filter((issue) => issue.severity === "warning").length;
  return (
    <div className={`report-migration-status ${job.status}`} aria-live="polite">
      <span>{importStatusLabel(job.status)}</span>
      <strong>{job.original_name}</strong>
      <small>{errorCount} 个错误 · {warningCount} 个警告 · 任务修订 {job.job_revision}</small>
      {job.error_message ? <p>{job.error_message}</p> : null}
    </div>
  );
}

function MigrationZoneHeading({ index, id, title, count }: { index: string; id: string; title: string; count?: string | number }) {
  return <div className="report-migration-zone-heading"><span>{index}</span><h4 id={id}>{title}</h4>{count !== undefined ? <strong>{count}</strong> : null}</div>;
}

function IssueCards({
  issues,
  readonly = false,
  drafts,
  onActionChange,
  onResolvedValueChange,
  resolutionActions = ["keep_original", "adopt_candidate", "skip"],
  isIssueEditable = isResolvableIssue
}: {
  issues: ReportImportIssue[];
  readonly?: boolean;
  drafts?: Record<number, ResolutionDraft>;
  onActionChange?: (issue: ReportImportIssue, action: ReportImportResolutionAction) => void;
  onResolvedValueChange?: (
    issue: ReportImportIssue,
    value: ResolutionScalar | undefined,
    mode: ResolutionDraft["valueMode"]
  ) => void;
  resolutionActions?: readonly ReportImportResolutionAction[];
  isIssueEditable?: (issue: ReportImportIssue) => boolean;
}) {
  if (!issues.length) return <p className="report-migration-empty">没有此类项目。</p>;
  return (
    <div className="report-migration-issue-list">
      {issues.map((issue) => {
        const draft = drafts?.[issue.id];
        const action = draft?.action ?? "keep_original";
        const compositeCandidate = isCompositeCandidate(issue.candidate_value);
        const candidateOptions = compositeCandidate ? resolutionCandidateOptions(issue.candidate_value) : [];
        const selectedCandidateIndex = candidateOptions.findIndex((option) => scalarValuesEqual(option.value, draft?.resolvedValue));
        const candidateSelection = draft?.valueMode === "manual"
          ? "manual"
          : selectedCandidateIndex >= 0 ? `candidate-${selectedCandidateIndex}` : "";
        return (
          <article className={`report-migration-issue ${issue.severity}`} key={issue.id}>
            <div className="report-migration-issue-heading">
              <span>{confidenceLabel(issue.confidence)}</span>
              <strong>{issue.field_path || issue.authority_field_id || issue.code}</strong>
              {issue.blocks_confirmation ? <em>阻断创建</em> : issue.blocks_final_export ? <em>阻断最终导出</em> : null}
            </div>
            <dl>
              <div><dt>源定位</dt><dd>{issue.source_locator || "—"}</dd></div>
              <div><dt>关联 ID</dt><dd>{issue.association_id || "未映射"}</dd></div>
            </dl>
            <div className="report-migration-values">
              <div><span>源原文</span><p>{issue.original_text || "（空）"}{issue.original_text_truncated ? "（展示已截断，完整原文保留在本地迁移记录中）" : ""}</p></div>
              <div><span>候选值</span><pre>{displayValue(issue.candidate_value)}</pre></div>
            </div>
            {!readonly && isIssueEditable(issue) ? (
              <fieldset className="report-migration-resolution-options">
                <legend>处理方式</legend>
                {resolutionActions.map((option) => (
                  <label key={option}>
                    <input type="radio" name={`issue-${issue.id}`} checked={action === option} onChange={() => onActionChange?.(issue, option)} />
                    {resolutionActionLabel(option)}
                  </label>
                ))}
                {action === "adopt_candidate" && compositeCandidate ? (
                  <label className="report-migration-field">
                    <span>采用的标量值</span>
                    <select
                      aria-label={`选择 ${issue.field_path || issue.code} 的标量候选`}
                      value={candidateSelection}
                      onChange={(event) => {
                        if (event.target.value === "manual") {
                          onResolvedValueChange?.(issue, undefined, "manual");
                          return;
                        }
                        if (!event.target.value) {
                          onResolvedValueChange?.(issue, undefined, undefined);
                          return;
                        }
                        const optionIndex = Number(event.target.value.replace("candidate-", ""));
                        const selected = candidateOptions[optionIndex];
                        onResolvedValueChange?.(issue, selected?.value, selected ? "candidate" : undefined);
                      }}
                    >
                      <option value="">请选择一个标量候选</option>
                      {candidateOptions.map((option, index) => (
                        <option key={`${scalarValueKey(option.value)}-${index}`} value={`candidate-${index}`}>{option.label}</option>
                      ))}
                      <option value="manual">人工填写标量</option>
                    </select>
                    {draft?.valueMode === "manual" ? (
                      <input
                        aria-label={`人工填写 ${issue.field_path || issue.code} 的标量值`}
                        type="text"
                        value={typeof draft.resolvedValue === "string" ? draft.resolvedValue : ""}
                        onChange={(event) => onResolvedValueChange?.(issue, event.target.value, "manual")}
                        placeholder="请输入非空文本"
                      />
                    ) : null}
                    <small>数组或对象不能整体写入；请选择其中一个标量，或人工填写非空文本。</small>
                  </label>
                ) : null}
              </fieldset>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

function SummaryValue({ value, emptyText }: { value: unknown; emptyText: string }) {
  if (Array.isArray(value)) {
    if (!value.length) return <p className="report-migration-empty">{emptyText}</p>;
    return <div className="report-migration-summary-cards">{value.map((item, index) => <pre key={index}>{displayValue(item)}</pre>)}</div>;
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (!entries.length) return <p className="report-migration-empty">{emptyText}</p>;
    return <dl className="report-migration-summary-list">{entries.map(([key, item]) => <div key={key}><dt>{key}</dt><dd>{displayValue(item)}</dd></div>)}</dl>;
  }
  return <p>{displayValue(value)}</p>;
}

function ReportReviewMetric({ label, value }: { label: string; value: number }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function buildResolutionDrafts(job: ReportImportJob): Record<number, ResolutionDraft> {
  return Object.fromEntries(job.issues.filter(isResolvableIssue).map((issue) => {
    const resolution = job.resolutions.find((item) => item.issue_id === issue.id);
    const candidateOptions = resolutionCandidateOptions(issue.candidate_value);
    const resolvedValue = resolution?.resolved_value;
    const valueMode = resolution?.action === "adopt_candidate" && isCompositeCandidate(issue.candidate_value)
      ? candidateOptions.some((option) => scalarValuesEqual(option.value, resolvedValue)) ? "candidate" : "manual"
      : undefined;
    return [issue.id, resolution
      ? { action: resolution.action, resolvedValue, valueMode }
      : { action: "keep_original" as const }];
  }));
}

function isResolvableIssue(issue: ReportImportIssue): boolean {
  return issue.needs_confirmation && !issue.blocks_confirmation;
}

function isPostCreateReviewIssue(job: ReportImportJob, issue: ReportImportIssue): boolean {
  if (job.status !== "succeeded" || !isResolvableIssue(issue)) return false;
  const resolution = job.resolutions.find((item) => item.issue_id === issue.id);
  return !resolution || !resolution.applied;
}

function isCompositeCandidate(value: unknown): value is unknown[] | Record<string, unknown> {
  return value !== null && typeof value === "object";
}

function isResolutionScalar(value: unknown): value is ResolutionScalar {
  if (typeof value === "string") return value.trim().length > 0;
  return typeof value === "number" && Number.isFinite(value);
}

function resolutionCandidateOptions(value: unknown): ResolutionCandidateOption[] {
  const entries: Array<[string, unknown]> = Array.isArray(value)
    ? value.map((item, index) => [`候选 ${index + 1}`, item])
    : isCompositeCandidate(value) ? Object.entries(value) : [];
  const seen = new Set<string>();
  return entries.flatMap(([label, candidate]) => {
    if (!isResolutionScalar(candidate)) return [];
    const key = scalarValueKey(candidate);
    if (seen.has(key)) return [];
    seen.add(key);
    return [{ label: `${label}：${displayValue(candidate)}`, value: candidate }];
  });
}

function scalarValueKey(value: ResolutionScalar): string {
  return `${typeof value}:${String(value)}`;
}

function scalarValuesEqual(left: ResolutionScalar, right: unknown): boolean {
  return isResolutionScalar(right) && scalarValueKey(left) === scalarValueKey(right);
}

function groupIssues(issues: ReportImportIssue[]) {
  return {
    automatic: issues.filter((issue) => !issue.needs_confirmation && (issue.confidence === "exact" || issue.confidence === "high")),
    pending: issues.filter((issue) => issue.needs_confirmation && issue.confidence !== "unmapped"),
    unmapped: issues.filter((issue) => issue.confidence === "unmapped")
  };
}

function appendixCandidatesFromSummary(value: unknown): AppendixSourceCandidate[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const candidate = item as Record<string, unknown>;
    if (typeof candidate.project_uuid !== "string" || typeof candidate.name !== "string") return [];
    return [{
      project_uuid: candidate.project_uuid,
      name: candidate.name,
      updated_at: typeof candidate.updated_at === "string" ? candidate.updated_at : null,
      sections_present: Array.isArray(candidate.sections_present)
        ? candidate.sections_present.filter((section): section is string => typeof section === "string")
        : [],
      validation_error_count: typeof candidate.validation_error_count === "number" ? candidate.validation_error_count : 0,
      complete: candidate.complete === true
    }];
  });
}

function summaryCount(value: unknown): number {
  if (Array.isArray(value)) return value.length;
  if (value && typeof value === "object") return Object.keys(value).length;
  if (typeof value === "number") return value;
  return 0;
}

function displayValue(value: unknown): string {
  if (value === undefined || value === null || value === "") return "（空）";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function suggestProjectName(fileName: string): string {
  return fileName.replace(/\.docx$/i, "").replace(/（客户复核版）/g, "").trim().slice(0, 120) || "迁移完整报告";
}

function shortHash(hash: string): string {
  return hash ? `${hash.slice(0, 10)}…${hash.slice(-8)}` : "—";
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function importStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    uploaded: "已上传", parsing: "安全扫描与解析中", preview_ready: "待审阅",
    confirming: "正在创建", succeeded: "迁移已创建", failed: "迁移失败"
  };
  return labels[status] ?? status;
}

function confidenceLabel(confidence: ReportImportIssue["confidence"]): string {
  return ({ exact: "精确", high: "高置信", ambiguous: "有歧义", unmapped: "未映射" } as const)[confidence];
}

function resolutionActionLabel(action: ReportImportResolutionAction): string {
  return ({ adopt_candidate: "采用候选", keep_original: "保留原文待确认", skip: "明确跳过" } as const)[action];
}

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
