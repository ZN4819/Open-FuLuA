import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import {
  confirmDerivedBlock,
  createDerivedGenerationRun,
  getDerivedGenerationReview,
  listDerivedRisks,
  overrideDerivedBlock,
  previewDerivedGeneration,
  runDerivedConsistencyCheck,
  updateDerivedRisk,
  type DerivedBlock,
  type DerivedGenerationRun,
  type DerivedIssue,
  type DerivedReview,
  type DerivedRisk,
  type DerivedRiskCollection,
  type GenerationImpact,
  type ThreatCatalogItem
} from "../api/reportClient.ts";
import { ReportExportWorkspace } from "./ReportExportWorkspace.tsx";
import { ReportRoundtripWorkspace } from "./ReportRoundtripWorkspace.tsx";

type ReportDerivedWorkspaceProps = {
  projectUuid: string;
  onDirtyChange: (dirty: boolean) => void;
  onChanged: () => void;
};

export function ReportDerivedWorkspace({
  projectUuid,
  onDirtyChange,
  onChanged
}: ReportDerivedWorkspaceProps) {
  const [impact, setImpact] = useState<GenerationImpact>();
  const [review, setReview] = useState<DerivedReview>();
  const [risks, setRisks] = useState<DerivedRiskCollection>();
  const [lastRun, setLastRun] = useState<DerivedGenerationRun>();
  const [dirtyKeys, setDirtyKeys] = useState<Set<string>>(new Set());
  const [isLoading, setIsLoading] = useState(true);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isChecking, setIsChecking] = useState(false);
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();
  const consistencyStatus = impact?.has_changes
    ? "stale"
    : review?.latest_consistency?.status ?? "not_generated";
  const hasUnsavedProjectChanges = [...dirtyKeys].some((key) => key !== "roundtrip:resolution");

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(undefined);
    try {
      const [nextImpact, nextReview, nextRisks] = await Promise.all([
        previewDerivedGeneration(projectUuid),
        getDerivedGenerationReview(projectUuid),
        listDerivedRisks(projectUuid)
      ]);
      setImpact(nextImpact);
      setReview(nextReview);
      setRisks(nextRisks);
    } catch (loadError) {
      setError(errorMessage(loadError, "读取正文生成工作台失败"));
    } finally {
      setIsLoading(false);
    }
  }, [projectUuid]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    onDirtyChange(dirtyKeys.size > 0);
  }, [dirtyKeys, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  const handleEditorDirty = useCallback((key: string, dirty: boolean) => {
    setDirtyKeys((current) => {
      const next = new Set(current);
      if (dirty) next.add(key);
      else next.delete(key);
      return next;
    });
  }, []);
  const handleRoundtripDirty = useCallback(
    (dirty: boolean) => handleEditorDirty("roundtrip:resolution", dirty),
    [handleEditorDirty]
  );

  async function handleGenerate() {
    if (!impact || dirtyKeys.size) return;
    setIsGenerating(true);
    setError(undefined);
    setMessage(undefined);
    try {
      const run = await createDerivedGenerationRun(projectUuid, impact.project_revision);
      setLastRun(run);
      setMessage(run.status === "current"
        ? "正文基线已按当前事实重新生成，请完成需要人工确认的内容。"
        : "生成已完成输入检查，请先处理下方缺失项或风险分析。"
      );
      await load();
      onChanged();
    } catch (generateError) {
      setError(errorMessage(generateError, "生成正文基线失败"));
    } finally {
      setIsGenerating(false);
    }
  }

  async function handleConsistencyCheck() {
    if (!review || dirtyKeys.size) return;
    setIsChecking(true);
    setError(undefined);
    setMessage(undefined);
    try {
      const result = await runDerivedConsistencyCheck(projectUuid, review.project_revision);
      setMessage(result.status === "valid"
        ? "一致性校验通过，当前派生上下文已可交给完整报告装配阶段。"
        : "一致性校验尚未通过，请处理下方未确认或已过期内容。"
      );
      await load();
      onChanged();
    } catch (checkError) {
      setError(errorMessage(checkError, "执行一致性校验失败"));
    } finally {
      setIsChecking(false);
    }
  }

  if (isLoading && !impact) {
    return <p className="report-loading" aria-live="polite">正在读取正文生成状态...</p>;
  }

  return (
    <div className="report-page-stack derived-workspace">
      <section className="report-page-heading">
        <p className="eyebrow">正文生成与一致性</p>
        <h3>从附录 A 生成权威结果、风险和正文</h3>
        <p>系统仅展示后端权威计算结果；人工只负责风险定级、允许覆盖的文案和最终确认。</p>
      </section>

      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="success-message" role="status">{message}</p> : null}

      <section className="report-form-card derived-generation-card">
        <div className="report-card-heading">
          <div>
            <p className="eyebrow">影响预览</p>
            <h4>生成基线</h4>
          </div>
          <StatusChip status={consistencyStatus} />
        </div>
        <div className="report-metric-grid">
          <ReportMetric label="受影响正文块" value={String(impact?.affected_blocks.length ?? 0)} />
          <ReportMetric label="待确认风险" value={String(risks?.items.filter((item) => item.confirmation_status !== "confirmed").length ?? 0)} />
          <ReportMetric label="待确认正文" value={String(review?.blocks.filter((item) => item.confirmation_status !== "confirmed").length ?? 0)} />
          <ReportMetric label="规则版本" value={lastSegment(impact?.rule_set_id ?? "", "-") || "—"} />
        </div>
        {impact?.issues.length ? <DerivedIssueList issues={impact.issues} /> : <p>当前事实源和附录 A 已通过生成前检查。</p>}
        {impact?.overrides_requiring_review.length ? (
          <p className="warning-text">有 {impact.overrides_requiring_review.length} 个人工版本将在重生成后要求复核。</p>
        ) : null}
        {impact?.has_changes && review?.latest_consistency?.status === "valid" ? (
          <p className="warning-text">上游事实已变化，原一致性结果已失效，请重新生成并复核。</p>
        ) : null}
        <div className="form-actions">
          <button
            type="button"
            onClick={() => void handleGenerate()}
            disabled={!impact?.can_generate || isGenerating || dirtyKeys.size > 0}
          >
            {isGenerating ? "正在生成..." : impact?.current_run_uuid ? "重新生成正文基线" : "生成正文基线"}
          </button>
          <button type="button" className="secondary-button" onClick={() => void load()} disabled={isGenerating}>刷新状态</button>
        </div>
      </section>

      {lastRun?.issues.length ? (
        <section className="report-form-card">
          <div className="report-card-heading"><h4>本次生成需要处理</h4><StatusChip status={lastRun.status} /></div>
          <DerivedIssueList issues={lastRun.issues} />
        </section>
      ) : null}

      <section className="report-form-card">
        <div className="report-card-heading">
          <div><p className="eyebrow">风险分析</p><h4>逐指标确认风险</h4></div>
          <span>{risks?.items.length ?? 0} 项</span>
        </div>
        {risks?.items.length ? (
          <div className="derived-card-list">
            {risks.items.map((risk) => (
              <RiskEditor
                key={`${risk.risk_uuid}:${risk.revision}`}
                projectUuid={projectUuid}
                projectRevision={risks.project_revision}
                risk={risk}
                threats={risks.threat_catalog}
                onDirtyChange={(dirty) => handleEditorDirty(`risk:${risk.risk_uuid}`, dirty)}
                onSaved={async () => { handleEditorDirty(`risk:${risk.risk_uuid}`, false); await load(); onChanged(); }}
              />
            ))}
          </div>
        ) : <p>当前没有部分符合或不符合指标，无需录入风险。</p>}
      </section>

      <section className="report-form-card">
        <div className="report-card-heading">
          <div><p className="eyebrow">正文复核</p><h4>生成基线、人工版本与确认状态</h4></div>
          <span>{review?.blocks.length ?? 0} 块</span>
        </div>
        {review?.blocks.length ? (
          <div className="derived-card-list">
            {review.blocks.map((block) => (
              <DerivedBlockCard
                key={`${block.block_uuid}:${block.revision_uuid}`}
                projectUuid={projectUuid}
                projectRevision={review.project_revision}
                block={block}
                onDirtyChange={(dirty) => handleEditorDirty(`block:${block.block_uuid}`, dirty)}
                onSaved={async () => { handleEditorDirty(`block:${block.block_uuid}`, false); await load(); onChanged(); }}
              />
            ))}
          </div>
        ) : <p>尚未生成正文基线。</p>}
      </section>

      <section className="report-form-card derived-consistency-card">
        <div className="report-card-heading">
          <div><p className="eyebrow">交付闸门</p><h4>一致性校验</h4></div>
          <StatusChip status={consistencyStatus} />
        </div>
        {review?.latest_consistency?.issues.length
          ? <DerivedIssueList issues={review.latest_consistency.issues} />
          : <p>{review?.latest_consistency?.status === "valid" ? "当前 revision 已通过一致性校验。" : "完成风险和正文确认后执行校验。"}</p>}
        <button
          type="button"
          onClick={() => void handleConsistencyCheck()}
          disabled={!review?.current_run_uuid || isChecking || dirtyKeys.size > 0}
        >
          {isChecking ? "正在校验..." : "执行一致性校验"}
        </button>
      </section>

      <ReportExportWorkspace
        projectUuid={projectUuid}
        projectRevision={review?.project_revision ?? impact?.project_revision}
        hasUnsavedChanges={dirtyKeys.size > 0}
      />
      <ReportRoundtripWorkspace
        projectUuid={projectUuid}
        projectRevision={review?.project_revision ?? impact?.project_revision}
        hasUnsavedChanges={hasUnsavedProjectChanges}
        onDirtyChange={handleRoundtripDirty}
        onCommitted={async () => { await load(); onChanged(); }}
      />
    </div>
  );
}

function RiskEditor({
  projectUuid,
  projectRevision,
  risk,
  threats,
  onDirtyChange,
  onSaved
}: {
  projectUuid: string;
  projectRevision: number;
  risk: DerivedRisk;
  threats: ThreatCatalogItem[];
  onDirtyChange: (dirty: boolean) => void;
  onSaved: () => Promise<void>;
}) {
  const [riskLevel, setRiskLevel] = useState(risk.risk_level ?? "");
  const [threatIds, setThreatIds] = useState<string[]>(risk.threat_ids);
  const [analysis, setAnalysis] = useState(risk.analysis_override?.text ?? "");
  const [reason, setReason] = useState(risk.override_reason ?? "");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string>();
  const dirty = riskLevel !== (risk.risk_level ?? "")
    || threatIds.join("|") !== risk.threat_ids.join("|")
    || analysis !== (risk.analysis_override?.text ?? "")
    || reason !== (risk.override_reason ?? "");
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setIsSaving(true);
    setError(undefined);
    try {
      await updateDerivedRisk(projectUuid, risk, projectRevision, {
        risk_level: riskLevel ? riskLevel as "high" | "medium" | "low" : null,
        threat_ids: threatIds,
        analysis_text: analysis.trim() || null,
        override_reason: reason.trim(),
        confirm: true
      });
      await onSaved();
    } catch (saveError) {
      setError(errorMessage(saveError, "保存风险分析失败"));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <form className="derived-item-card" onSubmit={handleSubmit}>
      <div className="derived-item-heading">
        <div><strong>{risk.indicator_code} {risk.indicator_name}</strong><small>{risk.final_indicator_result}</small></div>
        <StatusChip status={risk.confirmation_status} />
      </div>
      <p>{risk.problem_description}</p>
      <div className="derived-form-grid">
        <label>风险等级<select value={riskLevel} onChange={(event) => setRiskLevel(event.target.value)}><option value="">请选择</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></label>
        <label>关联威胁（可多选）<select multiple size={6} value={threatIds} onChange={(event) => setThreatIds(Array.from(event.currentTarget.selectedOptions, (option) => option.value))}>{threats.map((threat) => <option key={threat.id} value={threat.id}>{threat.id}｜{threat.description}</option>)}</select></label>
      </div>
      <label>风险分析<textarea rows={3} value={analysis} onChange={(event) => setAnalysis(event.target.value)} placeholder="概括该指标问题的整体风险等级，不逐个分析对象" /></label>
      <label>调整理由<textarea rows={2} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="修改已保存的等级或分析时填写" /></label>
      {error ? <p className="error" role="alert">{error}</p> : null}
      <button type="submit" disabled={isSaving || !riskLevel || !threatIds.length}>{isSaving ? "保存中..." : "保存并确认风险"}</button>
    </form>
  );
}

function DerivedBlockCard({
  projectUuid,
  projectRevision,
  block,
  onDirtyChange,
  onSaved
}: {
  projectUuid: string;
  projectRevision: number;
  block: DerivedBlock;
  onDirtyChange: (dirty: boolean) => void;
  onSaved: () => Promise<void>;
}) {
  const mode = blockOverrideMode(block);
  const initialDraft = useMemo(() => blockOverrideDraft(block, mode), [block, mode]);
  const [draft, setDraft] = useState(initialDraft);
  const [reason, setReason] = useState(block.override_reason ?? "");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string>();
  const dirty = JSON.stringify(draft) !== JSON.stringify(initialDraft)
    || reason !== (block.override_reason ?? "");
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  async function saveOverride() {
    if (!mode || !reason.trim()) return;
    setIsSaving(true);
    setError(undefined);
    try {
      await overrideDerivedBlock(
        projectUuid,
        block.block_uuid,
        projectRevision,
        blockOverridePayload(mode, draft),
        reason.trim()
      );
      await onSaved();
    } catch (saveError) {
      setError(errorMessage(saveError, "保存人工版本失败"));
    } finally {
      setIsSaving(false);
    }
  }

  async function confirm(action: "confirm" | "keep_override" | "discard_override") {
    setIsSaving(true);
    setError(undefined);
    try {
      await confirmDerivedBlock(projectUuid, block.block_uuid, projectRevision, action);
      await onSaved();
    } catch (saveError) {
      setError(errorMessage(saveError, "确认正文失败"));
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <article className="derived-item-card">
      <div className="derived-item-heading"><strong>{blockLabel(block.block_key)}</strong><StatusChip status={`${block.generation_status}:${block.confirmation_status}`} /></div>
      <p className="derived-preview">{blockPreview(block.effective)}</p>
      {mode === "text" || mode === "situation" ? (
        <label>人工版本<textarea rows={3} value={String(draft.text ?? "")} onChange={(event) => setDraft({ text: event.target.value })} /></label>
      ) : null}
      {mode === "items" ? Object.entries(draft).map(([indicatorCode, value]) => (
        <label key={indicatorCode}>{indicatorCode}<textarea rows={2} value={String(value)} onChange={(event) => setDraft((current) => ({ ...current, [indicatorCode]: event.target.value }))} /></label>
      )) : null}
      {mode ? <label>覆盖理由<input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="保存人工版本时必填" /></label> : null}
      {error ? <p className="error" role="alert">{error}</p> : null}
      <div className="form-actions">
        {mode ? <button type="button" className="secondary-button" onClick={() => void saveOverride()} disabled={isSaving || !dirty || !reason.trim()}>保存人工版本</button> : null}
        {block.confirmation_status !== "confirmed" ? <button type="button" onClick={() => void confirm(block.override ? "keep_override" : "confirm")} disabled={isSaving || dirty}>确认当前内容</button> : null}
        {block.override ? <button type="button" className="secondary-button" onClick={() => void confirm("discard_override")} disabled={isSaving}>放弃人工版本并确认基线</button> : null}
      </div>
    </article>
  );
}

type OverrideMode = "text" | "situation" | "items" | null;

function blockOverrideMode(block: DerivedBlock): OverrideMode {
  if (block.edit_policy !== "overrideable") return null;
  if (block.block_key === "conclusion.system_summary") return "text";
  if (block.block_key.startsWith("overall_evaluation.layer.")) return "situation";
  if (block.block_key.startsWith("recommendations.layer.")) return "items";
  return null;
}

function blockOverrideDraft(block: DerivedBlock, mode: OverrideMode): Record<string, string> {
  if (mode === "text") return { text: String(block.effective.text ?? "") };
  if (mode === "situation") return { text: String(block.effective.situation_description ?? "") };
  if (mode === "items") {
    const items = Array.isArray(block.effective.items) ? block.effective.items : [];
    return Object.fromEntries(items.flatMap((item) => {
      if (!item || typeof item !== "object") return [];
      const record = item as Record<string, unknown>;
      return [[String(record.indicator_code ?? ""), String(record.text ?? "")]];
    }).filter(([key]) => key));
  }
  return {};
}

function blockOverridePayload(mode: Exclude<OverrideMode, null>, draft: Record<string, string>): Record<string, unknown> {
  if (mode === "text") return { text: draft.text ?? "" };
  if (mode === "situation") return { situation_description: draft.text ?? "" };
  return { items: draft };
}

function blockPreview(payload: Record<string, unknown>): string {
  if (typeof payload.text === "string") return payload.text;
  if (typeof payload.summary_text === "string") return payload.summary_text;
  if (Array.isArray(payload.problems)) return `问题描述 ${payload.problems.length} 项`;
  if (Array.isArray(payload.items)) return `改进建议 ${payload.items.length} 项`;
  if (Array.isArray(payload.rows)) return `风险分析 ${payload.rows.length} 项`;
  return "结构化内容已生成。";
}

function blockLabel(key: string): string {
  if (key === "conclusion.system_summary") return "评估结论页｜系统简介";
  if (key === "conclusion.assessment_summary") return "评估结论页｜测评情况简介";
  if (key.startsWith("overall_evaluation")) return `总体评价｜${lastSegment(key, ".")}`;
  if (key.startsWith("security_issues")) return `安全问题｜${lastSegment(key, ".")}`;
  if (key.startsWith("recommendations")) return `改进建议｜${lastSegment(key, ".")}`;
  if (key.startsWith("risk_analysis")) return `风险分析｜${lastSegment(key, ".")}`;
  if (key === "assessment_conclusion") return "第 7 章｜评估结论";
  return key;
}

function StatusChip({ status }: { status: string }) {
  const labels: Record<string, string> = {
    valid: "已通过", invalid: "未通过", needs_input: "待补充", not_generated: "未生成", stale: "已过期",
    current: "当前", failed: "失败", confirmed: "已确认", unconfirmed: "待确认", review_required: "待复核",
    "current:confirmed": "当前·已确认", "current:unconfirmed": "当前·待确认", "current:review_required": "当前·待复核",
    "stale:confirmed": "已过期", "stale:unconfirmed": "已过期", "stale:review_required": "已过期"
  };
  return <span className={`derived-status ${status.replace(/[^a-z_:-]/gi, "-")}`}>{labels[status] ?? status}</span>;
}

function DerivedIssueList({ issues }: { issues: DerivedIssue[] }) {
  return <ul className="derived-issue-list">{issues.map((issue, index) => <li key={`${issue.code}:${issue.field ?? ""}:${index}`}><strong>{issue.code}</strong><span>{issue.message}</span>{issue.indicator ? <small>{issue.section_code}｜{issue.indicator}</small> : null}</li>)}</ul>;
}

function ReportMetric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function lastSegment(value: string, separator: string): string {
  const parts = value.split(separator);
  return parts[parts.length - 1] ?? "";
}
