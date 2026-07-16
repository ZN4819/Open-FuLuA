import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "../api/client.ts";
import {
  commitReportRoundtripJob,
  createReportRoundtripJob,
  getReportRoundtripDiff,
  getReportRoundtripIssues,
  getReportRoundtripJob,
  updateReportRoundtripResolution,
  type ReportRoundtripCommitResult,
  type ReportRoundtripDiff,
  type ReportRoundtripDiffGroup,
  type ReportRoundtripDiffItem,
  type ReportRoundtripDiffSummary,
  type ReportRoundtripIssue,
  type ReportRoundtripIssueCollection,
  type ReportRoundtripJob,
  type ReportRoundtripResolutionAction
} from "../api/reportRoundtripClient.ts";

type ReportRoundtripWorkspaceProps = {
  projectUuid: string;
  projectRevision?: number;
  hasUnsavedChanges: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onCommitted: () => void | Promise<void>;
};

const PROCESSING_STATUSES = new Set<ReportRoundtripJob["status"]>(["uploaded", "validating", "committing"]);
const REVIEW_STATUSES = new Set<ReportRoundtripJob["status"]>(["diff_ready", "conflicts_pending", "ready_to_commit", "succeeded"]);

export function ReportRoundtripWorkspace({
  projectUuid,
  projectRevision,
  hasUnsavedChanges,
  onDirtyChange,
  onCommitted
}: ReportRoundtripWorkspaceProps) {
  const [file, setFile] = useState<File>();
  const [job, setJob] = useState<ReportRoundtripJob>();
  const [diff, setDiff] = useState<ReportRoundtripDiff>();
  const [issues, setIssues] = useState<ReportRoundtripIssueCollection>();
  const [decisions, setDecisions] = useState<Record<string, ReportRoundtripResolutionAction>>({});
  const [resolutionHash, setResolutionHash] = useState<string>();
  const [decisionsDirty, setDecisionsDirty] = useState(false);
  const [commitResult, setCommitResult] = useState<ReportRoundtripCommitResult>();
  const [isWorking, setIsWorking] = useState(false);
  const [message, setMessage] = useState<string>();
  const [error, setError] = useState<string>();
  const operationRef = useRef(0);

  useEffect(() => () => { operationRef.current += 1; }, []);
  useEffect(() => { onDirtyChange(decisionsDirty); }, [decisionsDirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  const items = useMemo(() => allDiffItems(diff), [diff]);
  const groups = useMemo(() => diffGroups(diff), [diff]);
  const conflicts = useMemo(() => items.filter((item) => item.disposition === "conflict"), [items]);
  const ignoredItems = useMemo(
    () => uniqueDiffItems([
      ...items.filter((item) => item.disposition === "ignored"),
      ...(diff?.ignored_changes ?? [])
    ]),
    [diff, items]
  );
  const summary = useMemo(() => diffSummary(diff, items, ignoredItems), [diff, items, ignoredItems]);
  const resolvedConflictCount = conflicts.filter((item) => Boolean(decisions[decisionKey(item)])).length;
  const allConflictsResolved = resolvedConflictCount === conflicts.length;
  const expectedProjectRevision = diff?.observed_project_revision ?? job?.observed_project_revision ?? projectRevision;
  const effectiveResolutionHash = resolutionHash ?? job?.resolution_hash ?? undefined;
  const uploadBlocked = !projectRevision || hasUnsavedChanges || isWorking;
  const canSaveResolution = Boolean(
    job && diff?.diff_hash && expectedProjectRevision && conflicts.length > 0 && allConflictsResolved &&
    (job.status === "diff_ready" || job.status === "conflicts_pending") && !hasUnsavedChanges && !isWorking
  );
  const canCommit = Boolean(
    job?.status === "ready_to_commit" && effectiveResolutionHash && expectedProjectRevision &&
    allConflictsResolved && !decisionsDirty && !hasUnsavedChanges && !isWorking
  );

  async function handleUpload() {
    if (!file || uploadBlocked) return;
    if (!file.name.toLowerCase().endsWith(".docx")) {
      setError("请选择由本工具生成的可回收 DOCX 草稿。");
      return;
    }
    const operation = ++operationRef.current;
    setIsWorking(true);
    setError(undefined);
    setMessage(undefined);
    setJob(undefined);
    setDiff(undefined);
    setIssues(undefined);
    setDecisions({});
    setDecisionsDirty(false);
    setResolutionHash(undefined);
    setCommitResult(undefined);
    try {
      const created = await createReportRoundtripJob(projectUuid, file);
      if (operation !== operationRef.current) return;
      setJob(created);
      setMessage("文件已上传到隔离区，正在验证来源、签名、结构和未接受修订。");
      const current = await pollRoundtripJob(created, operation, operationRef);
      if (!current || operation !== operationRef.current) return;
      setJob(current);
      await loadArtifacts(current, operation);
      if (current.status === "invalid" || current.status === "failed") {
        setError(current.error_message || "文档未通过受控回收检查，请按问题清单修复后重新上传。");
      } else if (current.status === "stale") {
        resetStaleResolution();
      } else if (REVIEW_STATUSES.has(current.status)) {
        setMessage(current.status === "ready_to_commit"
          ? "三方差异已确认，可以提交回写。"
          : "三方差异已生成；冲突项必须逐项选择后才能提交。"
        );
      }
    } catch (uploadError) {
      if (operation === operationRef.current) setError(errorMessage(uploadError, "Word 回收任务创建失败"));
    } finally {
      if (operation === operationRef.current) setIsWorking(false);
    }
  }

  async function handleRefresh() {
    if (!job || isWorking) return;
    const operation = ++operationRef.current;
    setIsWorking(true);
    setError(undefined);
    try {
      const current = await getReportRoundtripJob(job.id);
      if (operation !== operationRef.current) return;
      setJob(current);
      await loadArtifacts(current, operation);
      if (current.status === "stale") resetStaleResolution();
    } catch (refreshError) {
      if (operation === operationRef.current) setError(errorMessage(refreshError, "刷新 Word 回收任务失败"));
    } finally {
      if (operation === operationRef.current) setIsWorking(false);
    }
  }

  async function loadArtifacts(current: ReportRoundtripJob, operation: number) {
    const issueResult = await getReportRoundtripIssues(current.id);
    if (operation !== operationRef.current) return;
    setIssues(issueResult);
    if (!REVIEW_STATUSES.has(current.status)) return;
    const nextDiff = await getReportRoundtripDiff(current.id);
    if (operation !== operationRef.current) return;
    setDiff(nextDiff);
    const restoredDecisions: Record<string, ReportRoundtripResolutionAction> = {};
    allDiffItems(nextDiff).forEach((item) => {
      if (item.disposition === "conflict" && item.resolution) restoredDecisions[decisionKey(item)] = item.resolution;
    });
    setDecisions(restoredDecisions);
    setDecisionsDirty(false);
    setResolutionHash(current.resolution_hash ?? undefined);
  }

  function chooseResolution(item: ReportRoundtripDiffItem, action: ReportRoundtripResolutionAction) {
    setDecisions((current) => ({ ...current, [decisionKey(item)]: action }));
    setDecisionsDirty(true);
    setResolutionHash(undefined);
    setMessage(undefined);
  }

  async function handleSaveResolutions() {
    if (!job || !diff || !expectedProjectRevision || !canSaveResolution) return;
    const missingIdentity = conflicts.find((item) => item.conflict_id === null || item.conflict_id === undefined);
    if (missingIdentity) {
      setError(`冲突项 ${missingIdentity.field_path} 缺少服务端 conflict_id，不能保存决议。`);
      return;
    }
    const operation = ++operationRef.current;
    setIsWorking(true);
    setError(undefined);
    setMessage(undefined);
    try {
      const saved = await updateReportRoundtripResolution(job.id, {
        diff_hash: diff.diff_hash,
        expected_project_revision: expectedProjectRevision,
        resolutions: conflicts.map((item) => ({
          conflict_id: item.conflict_id as number | string,
          action: decisions[decisionKey(item)]
        }))
      });
      if (operation !== operationRef.current) return;
      setResolutionHash(saved.resolution_hash);
      setDecisionsDirty(false);
      const current = await getReportRoundtripJob(job.id);
      if (operation !== operationRef.current) return;
      setJob(current);
      setMessage(current.status === "ready_to_commit" ? "冲突决议已保存，可以提交回写。" : "冲突决议已保存。" );
    } catch (resolutionError) {
      if (operation === operationRef.current) {
        const refreshedStale = await refreshAfterRoundtripError(resolutionError, operation);
        if (!refreshedStale && operation === operationRef.current) {
          setError(errorMessage(resolutionError, "保存冲突决议失败"));
        }
      }
    } finally {
      if (operation === operationRef.current) setIsWorking(false);
    }
  }

  async function handleCommit() {
    if (!job || !effectiveResolutionHash || !expectedProjectRevision || !canCommit) return;
    const operation = ++operationRef.current;
    setIsWorking(true);
    setError(undefined);
    setMessage("正在原子回写允许字段，并由后端重新计算评分和正文派生结果。");
    try {
      const committed = await commitReportRoundtripJob(job.id, {
        resolution_hash: effectiveResolutionHash,
        expected_project_revision: expectedProjectRevision
      });
      if (operation !== operationRef.current) return;
      setCommitResult(committed);
      if (committed.status === "stale") {
        setJob((current) => current ? { ...current, status: "stale" } : current);
        resetStaleResolution();
        return;
      }
      let current = await getReportRoundtripJob(job.id);
      if (operation !== operationRef.current) return;
      current = await pollRoundtripJob(current, operation, operationRef) ?? current;
      if (operation !== operationRef.current) return;
      setJob(current);
      await loadArtifacts(current, operation);
      if (current.status !== "succeeded") {
        setError(current.error_message || "Word 修改回写失败，项目数据未部分提交。");
        return;
      }
      await onCommitted();
      if (operation !== operationRef.current) return;
      setMessage("Word 修改已原子回写，项目数据、评分及派生正文已由后端重新计算。请复核受影响内容。" );
    } catch (commitError) {
      if (operation === operationRef.current) {
        const refreshedStale = await refreshAfterRoundtripError(commitError, operation);
        if (!refreshedStale && operation === operationRef.current) {
          setError(errorMessage(commitError, "提交 Word 修改失败"));
        }
      }
    } finally {
      if (operation === operationRef.current) setIsWorking(false);
    }
  }

  function resetStaleResolution() {
    setDecisions({});
    setDecisionsDirty(false);
    setResolutionHash(undefined);
    setError("项目数据已变化，旧差异和冲突决议已经失效。请重新上传可回收草稿生成新任务。" );
  }

  async function refreshAfterRoundtripError(mutationError: unknown, operation: number): Promise<boolean> {
    if (!job) return false;
    const staleByCode = mutationError instanceof ApiError && [
      "ROUNDTRIP_PROJECT_REVISION_STALE",
      "ROUNDTRIP_DATABASE_VALUE_STALE"
    ].includes(mutationError.code ?? "");
    try {
      const current = await getReportRoundtripJob(job.id);
      if (operation !== operationRef.current) return true;
      setJob(current);
      if (current.status === "stale" || staleByCode) {
        resetStaleResolution();
        return true;
      }
    } catch {
      if (operation !== operationRef.current) return true;
      // A stable stale response remains authoritative even when the follow-up
      // status request fails; disable the old local resolution immediately.
      if (staleByCode) {
        setJob((current) => current ? { ...current, status: "stale" } : current);
        resetStaleResolution();
        return true;
      }
    }
    return false;
  }

  const displayedIssues = [...(issues?.errors ?? []), ...(issues?.warnings ?? []), ...(issues?.info ?? [])];
  const predictedApply = summary.apply_word + conflicts.filter((item) => decisions[decisionKey(item)] === "apply_word").length;
  const predictedKeep = summary.keep_database + conflicts.filter((item) => decisions[decisionKey(item)] === "keep_database").length;

  return (
    <section className="report-form-card report-roundtrip-card" aria-label="受控 Word 回收">
      <div className="report-card-heading">
        <div><p className="eyebrow">R7 受控 Word 往返</p><h4>回收 Word 修改</h4></div>
        <span className={`derived-status ${job?.status ?? "not_generated"}`}>{roundtripStatusLabel(job?.status)}</span>
      </div>
      <p>仅接受本项目生成且明确标记为“可回收”的草稿。外部报告、普通草稿和正式版不能在此更新项目。</p>

      <div className="report-roundtrip-notice" role="note">
        <strong>上传前请确认</strong>
        <ul>
          <li>只修改已有白名单字段和已有业务行，不新增、删除、复制、拆分、合并或重排行。</li>
          <li>先在 Microsoft Word 中接受或拒绝全部修订；未接受修订会直接阻断回收。</li>
          <li>图片、题注、引用、页码、域缓存和格式变化不会回收，必须回到工具内修改。</li>
          <li>机构常量、对象/单元/综合分值、统计、风险汇总和结论等只读派生值不会回收。</li>
        </ul>
      </div>

      <div className="report-roundtrip-upload">
        <label>
          <span>可回收 DOCX 草稿</span>
          <input
            type="file"
            accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            disabled={isWorking}
            onChange={(event) => {
              setFile(event.target.files?.[0]);
              setError(undefined);
            }}
          />
          <small>{file?.name ?? "尚未选择文件"}</small>
        </label>
        <button type="button" onClick={() => void handleUpload()} disabled={!file || uploadBlocked}>
          {isWorking && (!job || PROCESSING_STATUSES.has(job.status)) ? "正在检查..." : "上传并生成差异"}
        </button>
        {job ? <button type="button" className="secondary-button" onClick={() => void handleRefresh()} disabled={isWorking}>刷新任务</button> : null}
      </div>
      {hasUnsavedChanges ? <p className="warning-text">当前有未保存内容，请保存后再上传或提交 Word 修改。</p> : null}
      {!projectRevision ? <p className="warning-text">尚未生成可供回收校验的项目 revision。</p> : null}
      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="success-message" role="status">{message}</p> : null}

      {job ? <RoundtripJobFacts job={job} /> : null}
      {displayedIssues.length ? <RoundtripIssueList issues={displayedIssues} /> : null}

      {diff ? (
        <div className="report-roundtrip-review">
          <section className="report-roundtrip-summary" aria-label="三方差异汇总">
            <RoundtripMetric label="可应用 Word" value={summary.apply_word} tone="success" />
            <RoundtripMetric label="保留数据库" value={summary.keep_database} />
            <RoundtripMetric label="已一致" value={summary.already_equal} />
            <RoundtripMetric label="待解决冲突" value={summary.conflicts} tone={summary.conflicts ? "warning" : undefined} />
            <RoundtripMetric label="不会回收" value={summary.ignored} />
          </section>

          {groups.map((group) => (
            <RoundtripDiffGroupView
              key={group.group_key}
              group={group}
              decisions={decisions}
              readonly={!job || job.status === "ready_to_commit" || job.status === "succeeded" || job.status === "stale"}
              onChoose={chooseResolution}
            />
          ))}

          {ignoredItems.length ? (
            <section className="report-roundtrip-ignored" aria-label="不会回收的修改">
              <div className="report-card-heading"><h5>不会回收的修改</h5><span>{ignoredItems.length} 项</span></div>
              <p>以下变化仅作提示，不会复制图片或覆盖工具中的只读、格式及派生数据。</p>
              <ul>{ignoredItems.map((item) => <li key={`ignored:${diffItemKey(item)}`}><strong>{item.field_label || item.field_path}</strong><span>{item.ignored_reason || "该字段不在 Word 回收白名单内。"}</span></li>)}</ul>
            </section>
          ) : null}

          {conflicts.length ? (
            <div className="report-roundtrip-resolution-bar">
              <p>已处理 {resolvedConflictCount}/{conflicts.length} 个冲突。每个冲突只能保留数据库值或采用 Word 值。</p>
              <button type="button" onClick={() => void handleSaveResolutions()} disabled={!canSaveResolution}>
                {isWorking ? "正在保存..." : "保存全部冲突决议"}
              </button>
            </div>
          ) : <p className="success-message">没有需要人工选择的冲突，系统已按三方规则生成自动处理计划。</p>}

          <div className="report-roundtrip-commit">
            <div>
              <strong>预计提交结果</strong>
              <p>更新 {predictedApply} 个字段，保留数据库 {predictedKeep} 个字段，忽略 {summary.ignored} 项。</p>
            </div>
            <button type="button" onClick={() => void handleCommit()} disabled={!canCommit}>
              {isWorking && job?.status === "committing" ? "正在原子回写..." : "提交 Word 修改"}
            </button>
          </div>
          {job?.status === "ready_to_commit" && !effectiveResolutionHash ? <p className="error">服务端尚未提供 resolution hash，请刷新任务后重试。</p> : null}
        </div>
      ) : null}

      {commitResult?.after_revision ? (
        <p className="report-roundtrip-audit">审计结果：revision {commitResult.before_revision} → {commitResult.after_revision}；实际更新 {commitResult.applied_fields} 个字段。</p>
      ) : null}
    </section>
  );
}

async function pollRoundtripJob(
  initial: ReportRoundtripJob,
  operation: number,
  operationRef: { current: number }
): Promise<ReportRoundtripJob | undefined> {
  let current = initial;
  for (let attempt = 0; attempt < 600; attempt += 1) {
    if (!PROCESSING_STATUSES.has(current.status)) return current;
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
    if (operation !== operationRef.current) return undefined;
    current = await getReportRoundtripJob(current.id);
  }
  throw new Error("Word 回收任务等待超时，请稍后刷新任务状态。" );
}

function RoundtripJobFacts({ job }: { job: ReportRoundtripJob }) {
  return (
    <dl className="report-roundtrip-facts">
      <div><dt>任务</dt><dd>{job.id}</dd></div>
      <div><dt>来源文件</dt><dd>{job.original_name}</dd></div>
      <div><dt>基线 revision</dt><dd>{job.base_project_revision}</dd></div>
      <div><dt>比对 revision</dt><dd>{job.observed_project_revision}</dd></div>
      <div><dt>来源快照</dt><dd>{job.source_snapshot_id ?? "—"}</dd></div>
      <div><dt>diff 摘要</dt><dd>{shortHash(job.diff_hash)}</dd></div>
      <div><dt>manifest</dt><dd>{shortHash(job.manifest_hash)}</dd></div>
      <div><dt>可写契约</dt><dd>{shortHash(job.writable_contract_hash)}</dd></div>
    </dl>
  );
}

function RoundtripIssueList({ issues }: { issues: ReportRoundtripIssue[] }) {
  return (
    <div className="report-roundtrip-issues">
      <div className="report-card-heading"><h5>校验与回收问题</h5><span>{issues.length} 项</span></div>
      <ul>
        {issues.map((issue, index) => (
          <li key={`${issue.code}:${issue.field_id ?? issue.row_id ?? index}`} className={issue.severity}>
            <div><span>{issue.severity === "error" ? "阻断" : issue.severity === "warning" ? "警告" : "提示"}</span><strong>{issue.code}</strong></div>
            <p>{issue.message}</p>
            {issueLocation(issue) ? <small>{issueLocation(issue)}</small> : null}
            {issue.remediation ? <em>处理建议：{issue.remediation}</em> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function RoundtripDiffGroupView({
  group,
  decisions,
  readonly,
  onChoose
}: {
  group: ReportRoundtripDiffGroup;
  decisions: Record<string, ReportRoundtripResolutionAction>;
  readonly: boolean;
  onChoose: (item: ReportRoundtripDiffItem, action: ReportRoundtripResolutionAction) => void;
}) {
  const visibleItems = group.items.filter((item) => item.disposition !== "ignored");
  if (!visibleItems.length) return null;
  return (
    <details className="report-roundtrip-group" open>
      <summary>
        <strong>{[group.section_code, group.section_title, group.object_name].filter(Boolean).join("｜") || "项目字段"}</strong>
        <span>{visibleItems.length} 项</span>
      </summary>
      <div className="report-roundtrip-diff-list">
        {visibleItems.map((item) => {
          const selected = decisions[decisionKey(item)];
          return (
            <article key={diffItemKey(item)} className={`report-roundtrip-diff ${item.disposition}`}>
              <header><div><strong>{item.field_label || item.field_path}</strong><small>{item.field_path}</small></div><span>{dispositionLabel(item.disposition)}</span></header>
              <div className="report-roundtrip-values">
                <DiffValue label="导出基线 B" value={item.base_value} />
                <DiffValue label="工具当前值 D" value={item.database_value} />
                <DiffValue label="Word 值 W" value={item.word_value} />
              </div>
              {item.disposition === "conflict" ? (
                <fieldset disabled={readonly}>
                  <legend>冲突处理</legend>
                  <label><input type="radio" name={`roundtrip:${decisionKey(item)}`} checked={selected === "keep_database"} onChange={() => onChoose(item, "keep_database")} />保留数据库当前值</label>
                  <label><input type="radio" name={`roundtrip:${decisionKey(item)}`} checked={selected === "apply_word"} onChange={() => onChoose(item, "apply_word")} />采用 Word 值</label>
                </fieldset>
              ) : null}
            </article>
          );
        })}
      </div>
    </details>
  );
}

function DiffValue({ label, value }: { label: string; value: unknown }) {
  return <div><span>{label}</span><pre>{displayValue(value)}</pre></div>;
}

function RoundtripMetric({ label, value, tone }: { label: string; value: number; tone?: "success" | "warning" }) {
  return <div className={tone ?? ""}><span>{label}</span><strong>{value}</strong></div>;
}

function allDiffItems(diff?: ReportRoundtripDiff): ReportRoundtripDiffItem[] {
  if (!diff) return [];
  if (diff.groups?.length) return diff.groups.flatMap((group) => group.items);
  return diff.items ?? [];
}

function diffGroups(diff?: ReportRoundtripDiff): ReportRoundtripDiffGroup[] {
  if (!diff) return [];
  if (diff.groups?.length) return diff.groups;
  const groups = new Map<string, ReportRoundtripDiffGroup>();
  (diff.items ?? []).forEach((item) => {
    const key = [item.section_code ?? "project", item.object_name ?? ""].join(":");
    const group = groups.get(key) ?? {
      group_key: key,
      section_code: item.section_code,
      section_title: item.section_title,
      object_name: item.object_name,
      items: []
    };
    group.items.push(item);
    groups.set(key, group);
  });
  return [...groups.values()];
}

function diffSummary(
  diff: ReportRoundtripDiff | undefined,
  items: ReportRoundtripDiffItem[],
  ignoredItems: ReportRoundtripDiffItem[]
): ReportRoundtripDiffSummary {
  const counted = (disposition: ReportRoundtripDiffItem["disposition"]) => items.filter((item) => item.disposition === disposition).length;
  return {
    total: diff?.summary?.total ?? items.length,
    unchanged: diff?.summary?.unchanged ?? counted("unchanged"),
    keep_database: diff?.summary?.keep_database ?? counted("keep_database"),
    apply_word: diff?.summary?.apply_word ?? counted("apply_word"),
    already_equal: diff?.summary?.already_equal ?? counted("already_equal"),
    conflicts: diff?.summary?.conflicts ?? counted("conflict"),
    ignored: diff?.summary?.ignored ?? ignoredItems.length
  };
}

function uniqueDiffItems(items: ReportRoundtripDiffItem[]): ReportRoundtripDiffItem[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const key = diffItemKey(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function decisionKey(item: ReportRoundtripDiffItem): string {
  return String(item.conflict_id ?? item.id);
}

function diffItemKey(item: ReportRoundtripDiffItem): string {
  return [item.id, item.conflict_id ?? "", item.field_path, item.row_id ?? ""].join(":");
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "（空）";
  if (typeof value === "object" && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    const display = record.display_value ?? record.display;
    if (typeof display === "string") return truncate(display);
  }
  if (typeof value === "string") return truncate(value);
  try {
    return truncate(JSON.stringify(value, null, 2));
  } catch {
    return truncate(String(value));
  }
}

function truncate(value: string): string {
  return value.length > 600 ? `${value.slice(0, 600)}\n…（内容已截断）` : value;
}

function issueLocation(issue: ReportRoundtripIssue): string {
  return [issue.section_code, issue.object_name, issue.field_path ?? issue.field_id, issue.row_id].filter(Boolean).join(" / ");
}

function dispositionLabel(disposition: ReportRoundtripDiffItem["disposition"]): string {
  return ({
    unchanged: "未修改",
    keep_database: "保留数据库",
    apply_word: "自动采用 Word",
    already_equal: "已一致",
    conflict: "需要选择",
    ignored: "不会回收"
  } as const)[disposition];
}

function roundtripStatusLabel(status?: ReportRoundtripJob["status"]): string {
  if (!status) return "未上传";
  return ({
    uploaded: "已上传",
    validating: "校验中",
    invalid: "校验未通过",
    diff_ready: "差异已生成",
    conflicts_pending: "待处理冲突",
    ready_to_commit: "可提交",
    committing: "提交中",
    succeeded: "已完成",
    failed: "失败",
    stale: "已失效"
  } as const)[status];
}

function shortHash(value?: string | null): string {
  return value ? `${value.slice(0, 12)}…` : "—";
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
