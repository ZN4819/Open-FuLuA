import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, resolveFileUrl } from "../api/client.ts";
import {
  createAppendixBRecord,
  deleteAppendixBItem,
  getAppendixB,
  reorderAppendixBRecords,
  replaceAppendixBImage,
  updateAppendixBCategory,
  updateAppendixBImage,
  updateAppendixBRecord,
  uploadAppendixBImages,
  validateAppendixB,
  type AppendixBCategory,
  type AppendixBEvidenceItem,
  type AppendixBIssue,
  type AppendixBRecordInput,
  type AppendixBWorkspace,
  type ReportMember,
  type ReportOrganization
} from "../api/reportClient.ts";

type Props = {
  projectUuid: string;
  focusTableCode?: string;
  onDirtyChange: (dirty: boolean) => void;
  onChanged: () => void;
};

type Option = { value: string; label: string };

const RECORD_SUBTYPES: Record<string, Option[]> = {
  engagement_proof: [{ value: "engagement", label: "委托证明" }],
  travel_accommodation: [{ value: "travel", label: "差旅记录" }],
  onsite_process: [{ value: "visit", label: "进场记录" }],
  authorization_notice: [
    { value: "authorization", label: "现场测评授权书" },
    { value: "risk_notice", label: "风险告知书" }
  ],
  plan_review: [{ value: "plan_review", label: "方案评审" }],
  report_review: [{ value: "report_review", label: "报告评审" }],
  assessor_roster: [{ value: "member", label: "人员资格行" }],
  assessor_exam_proof: [{ value: "exam_proof", label: "成绩证明" }],
  grading_filing: [{ value: "filing", label: "定级备案证明" }]
};

const IMAGE_SUBTYPES: Record<string, Option[]> = {
  engagement_proof: [{ value: "engagement_document", label: "委托证明文件" }],
  travel_accommodation: [
    { value: "travel_ticket", label: "交通票证" },
    { value: "accommodation_bill", label: "住宿账单" },
    { value: "accommodation_invoice", label: "住宿发票" }
  ],
  onsite_process: [
    { value: "sign_in", label: "签到记录" },
    { value: "onsite_photo", label: "现场照片" },
    { value: "handover_record", label: "资料交接记录" },
    { value: "room_access_record", label: "进出机房记录" }
  ],
  authorization_notice: [
    { value: "authorization", label: "授权书" },
    { value: "risk_notice", label: "风险告知书" }
  ],
  plan_review: [
    { value: "review", label: "评审材料" },
    { value: "confirmation", label: "确认材料" }
  ],
  report_review: [{ value: "review", label: "评审材料" }],
  assessor_roster: [],
  assessor_exam_proof: [{ value: "exam_proof", label: "成绩证明" }],
  grading_filing: [{ value: "filing_proof", label: "备案证明" }]
};

const SINGLE_RECORD = new Set(["engagement_proof", "plan_review", "report_review", "grading_filing"]);
const ROLE_OPTIONS: Option[] = [
  { value: "member", label: "组员" },
  { value: "compiler", label: "组员、密评报告编制人" },
  { value: "reviewer", label: "密评报告审核人" },
  { value: "approver", label: "密评报告批准人" }
];

function messageOf(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

function defaultMetadata(categoryCode: string): Record<string, unknown> {
  if (categoryCode === "engagement_proof") return { file_type: "", amount: "", unit_price: "" };
  if (categoryCode === "travel_accommodation") return { is_local: false };
  if (categoryCode === "plan_review") return { plan_name: "" };
  if (categoryCode === "assessor_roster") return { role: "member" };
  if (categoryCode === "grading_filing") {
    return { filing_system_same: null, filing_system_name: "", difference: "" };
  }
  return { note: "" };
}

function recordDraft(category: AppendixBCategory, item?: AppendixBEvidenceItem): AppendixBRecordInput {
  const memberUuids = item?.usages
    .filter((usage) => ["member", "personnel_role", "exam_proof"].includes(usage.usage_kind))
    .map((usage) => usage.related_member_uuid)
    .filter((value): value is string => Boolean(value)) ?? [];
  const relatedItemUuids = item?.usages
    .filter((usage) => usage.usage_kind === "covered_onsite")
    .map((usage) => usage.related_item_uuid)
    .filter((value): value is string => Boolean(value)) ?? [];
  return {
    subtype: item?.subtype ?? RECORD_SUBTYPES[category.category_code]?.[0]?.value ?? "",
    title: item?.title ?? "",
    starts_on: item?.starts_on ?? null,
    ends_on: item?.ends_on ?? null,
    organization_uuid: item?.organization_uuid ?? null,
    location: item?.location ?? "",
    sort_order: item?.sort_order ?? category.items.filter((value) => value.item_kind === "record").length,
    metadata: item?.metadata ?? defaultMetadata(category.category_code),
    member_uuids: memberUuids,
    related_item_uuids: relatedItemUuids
  };
}

function metadataText(metadata: Record<string, unknown>, key: string): string {
  const value = metadata[key];
  return typeof value === "string" ? value : "";
}

export function ReportAppendixBWorkspace({ projectUuid, focusTableCode, onDirtyChange, onChanged }: Props) {
  const [workspace, setWorkspace] = useState<AppendixBWorkspace>();
  const [dirtyTokens, setDirtyTokens] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [validating, setValidating] = useState(false);
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();
  const [preview, setPreview] = useState<AppendixBEvidenceItem>();
  const [epoch, setEpoch] = useState(0);

  const setDirty = useCallback((token: string, dirty: boolean) => {
    setDirtyTokens((current) => {
      const next = new Set(current);
      if (dirty) next.add(token); else next.delete(token);
      return next;
    });
  }, []);

  useEffect(() => onDirtyChange(dirtyTokens.size > 0), [dirtyTokens, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  const load = useCallback(async (successMessage?: string) => {
    setLoading(true);
    setError(undefined);
    try {
      const next = await getAppendixB(projectUuid);
      setWorkspace(next);
      setDirtyTokens(new Set());
      setEpoch((value) => value + 1);
      if (successMessage) setMessage(successMessage);
    } catch (loadError) {
      setError(messageOf(loadError, "读取附录 B 证明材料失败。"));
    } finally {
      setLoading(false);
    }
  }, [projectUuid]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!focusTableCode || loading) return;
    document.getElementById(`appendix-b-${focusTableCode}`)?.scrollIntoView({ block: "start" });
  }, [focusTableCode, loading, epoch]);

  async function runValidation() {
    if (!workspace || dirtyTokens.size) return;
    setValidating(true);
    setError(undefined);
    try {
      const result = await validateAppendixB(projectUuid, workspace.project_revision);
      setMessage(result.valid
        ? `附录 B 关联校验通过；仍有 ${result.warnings.length} 项非阻断提示。`
        : `发现 ${result.errors.length} 项关联错误，请按表号修正。`);
      await load();
    } catch (validationError) {
      setError(messageOf(validationError, "附录 B 校验失败。"));
    } finally {
      setValidating(false);
    }
  }

  if (loading && !workspace) return <p className="report-loading">正在读取附录 B 九表证据...</p>;
  if (!workspace) {
    return <div className="report-workbench-failure" role="alert"><p>{error ?? "附录 B 工作台不可用。"}</p><button type="button" onClick={() => void load()}>重新读取</button></div>;
  }

  const onsiteRecords = workspace.categories
    .find((category) => category.category_code === "onsite_process")
    ?.items.filter((item) => item.item_kind === "record") ?? [];

  return (
    <div className="report-page-stack appendix-b-workspace">
      <section className="report-page-heading appendix-b-heading">
        <div>
          <p className="eyebrow">Appendix B Evidence</p>
          <h3>附录 B 密评活动有效性证明记录</h3>
          <p>九类证据固定对应母版表 B-1～表 B-9；缺材料为提示，人员、日期、单位或文件冲突会阻断正式导出。</p>
        </div>
        <div className="appendix-b-summary" aria-label="附录 B 完成情况">
          <strong>{workspace.completion.completed}/{workspace.completion.category_total}</strong>
          <span>已填写或不适用</span>
          <span className={workspace.completion.error_count ? "dirty-chip" : "clean-chip"}>{workspace.completion.error_count} 错误</span>
          <span className="warning-chip">{workspace.completion.warning_count} 提示</span>
        </div>
      </section>
      <div className="report-section-toolbar">
        <span className={dirtyTokens.size ? "dirty-chip" : "clean-chip"}>{dirtyTokens.size ? `${dirtyTokens.size} 处未保存` : `项目版本 ${workspace.project_revision}`}</span>
        <button type="button" className="secondary-button" disabled={loading || Boolean(dirtyTokens.size)} onClick={() => void load()}>刷新</button>
        <button type="button" disabled={validating || Boolean(dirtyTokens.size)} onClick={() => void runValidation()}>{validating ? "校验中..." : "校验附录 B"}</button>
      </div>
      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="success" aria-live="polite">{message}</p> : null}
      <div className="appendix-b-category-list">
        {workspace.categories.map((category) => (
          <AppendixBCategoryCard
            key={`${category.category_uuid}-${epoch}`}
            projectUuid={projectUuid}
            projectRevision={workspace.project_revision}
            category={category}
            members={workspace.members}
            organizations={workspace.organizations}
            onsiteRecords={onsiteRecords}
            focused={focusTableCode === category.code}
            setDirty={setDirty}
            onReload={(text) => { onChanged(); void load(text); }}
            onError={setError}
            onPreview={setPreview}
          />
        ))}
      </div>
      {preview ? (
        <div className="appendix-b-preview" role="dialog" aria-modal="true" aria-label={preview.caption || preview.original_name || "证据图片预览"} onClick={() => setPreview(undefined)}>
          <div onClick={(event) => event.stopPropagation()}>
            <button type="button" className="secondary-button" onClick={() => setPreview(undefined)}>关闭预览</button>
            <img src={resolveFileUrl(preview.file_url)} alt={preview.alt_text || preview.caption || "证据图片"} />
            <p>{preview.caption || preview.original_name}</p>
          </div>
        </div>
      ) : null}
    </div>
  );
}

type CardProps = {
  projectUuid: string;
  projectRevision: number;
  category: AppendixBCategory;
  members: ReportMember[];
  organizations: ReportOrganization[];
  onsiteRecords: AppendixBEvidenceItem[];
  focused: boolean;
  setDirty: (token: string, dirty: boolean) => void;
  onReload: (message: string) => void;
  onError: (message?: string) => void;
  onPreview: (item: AppendixBEvidenceItem) => void;
};

function AppendixBCategoryCard(props: CardProps) {
  const { category, projectRevision, projectUuid, setDirty, onReload, onError } = props;
  const [expanded, setExpanded] = useState(props.focused || category.items.length > 0 || category.errors.length > 0);
  const [adding, setAdding] = useState(false);
  const [isNotApplicable, setIsNotApplicable] = useState(category.is_not_applicable);
  const [reason, setReason] = useState(category.not_applicable_reason);
  const [acknowledge, setAcknowledge] = useState(Boolean(category.warning_acknowledged_at));
  const [savingCategory, setSavingCategory] = useState(false);
  const records = category.items.filter((item) => item.item_kind === "record");
  const images = category.items.filter((item) => item.item_kind === "image");
  const categoryDirty = isNotApplicable !== category.is_not_applicable
    || reason !== category.not_applicable_reason
    || acknowledge !== Boolean(category.warning_acknowledged_at);
  const categoryToken = `category:${category.category_code}`;
  useEffect(() => {
    setDirty(categoryToken, categoryDirty);
    return () => setDirty(categoryToken, false);
  }, [categoryDirty, categoryToken, setDirty]);
  useEffect(() => { if (props.focused) setExpanded(true); }, [props.focused]);

  async function saveCategory() {
    setSavingCategory(true);
    onError(undefined);
    try {
      await updateAppendixBCategory(projectUuid, category, projectRevision, {
        is_not_applicable: isNotApplicable,
        not_applicable_reason: reason,
        acknowledge_warning: acknowledge
      });
      onReload(`${category.code} 类别状态已保存。`);
    } catch (saveError) {
      onError(messageOf(saveError, `${category.code} 类别状态保存失败。`));
    } finally {
      setSavingCategory(false);
    }
  }

  async function moveRecord(item: AppendixBEvidenceItem, direction: -1 | 1) {
    const current = records.findIndex((value) => value.item_uuid === item.item_uuid);
    const target = current + direction;
    if (current < 0 || target < 0 || target >= records.length) return;
    const next = records.map((value) => value.item_uuid);
    [next[current], next[target]] = [next[target], next[current]];
    onError(undefined);
    try {
      await reorderAppendixBRecords(projectUuid, category.category_code, projectRevision, next);
      onReload(`${category.code} 记录顺序已更新。`);
    } catch (moveError) {
      onError(messageOf(moveError, "记录排序失败。"));
    }
  }

  const canAdd = !SINGLE_RECORD.has(category.category_code) || records.length === 0;
  return (
    <section id={`appendix-b-${category.code}`} className={`appendix-b-category ${props.focused ? "focused" : ""}`}>
      <button type="button" className="appendix-b-category-heading" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
        <span className="appendix-b-code">{category.code}</span>
        <span><strong>{category.title}</strong>{category.code === "B-7" || category.code === "B-8" ? <small>人员资格部分（母版目录 B.4）</small> : null}</span>
        <span className={`status-chip ${category.errors.length ? "failed" : category.completion === "empty" ? "pending" : "succeeded"}`}>
          {category.errors.length ? `${category.errors.length} 错误` : category.completion === "not_applicable" ? "不适用" : category.completion === "complete" ? `${records.length} 条记录` : "未填写"}
        </span>
        <span aria-hidden="true">{expanded ? "收起" : "展开"}</span>
      </button>
      {expanded ? (
        <div className="appendix-b-category-body">
          <div className="appendix-b-category-controls">
            <label className="checkbox-label"><input type="checkbox" checked={isNotApplicable} onChange={(event) => setIsNotApplicable(event.target.checked)} />本类别不适用</label>
            <label><span>不适用原因</span><input value={reason} disabled={!isNotApplicable} onChange={(event) => setReason(event.target.value)} placeholder="标记不适用时必填" /></label>
            {category.warnings.length ? <label className="checkbox-label"><input type="checkbox" checked={acknowledge} onChange={(event) => setAcknowledge(event.target.checked)} />已确认当前非阻断提示</label> : null}
            <button type="button" disabled={!categoryDirty || savingCategory || (isNotApplicable && !reason.trim())} onClick={() => void saveCategory()}>{savingCategory ? "保存中..." : "保存类别状态"}</button>
          </div>
          <AppendixBIssues issues={[...category.errors, ...category.warnings]} />
          <div className="appendix-b-record-list">
            {records.map((item, index) => (
              <AppendixBRecordEditor
                key={item.item_uuid}
                {...props}
                item={item}
                images={images.filter((image) => image.parent_item_uuid === item.item_uuid)}
                setDirty={setDirty}
                onMoveUp={index > 0 ? () => void moveRecord(item, -1) : undefined}
                onMoveDown={index < records.length - 1 ? () => void moveRecord(item, 1) : undefined}
              />
            ))}
          </div>
          {adding ? (
            <AppendixBRecordEditor
              {...props}
              images={[]}
              setDirty={setDirty}
              onCancel={() => setAdding(false)}
            />
          ) : null}
          {!adding && canAdd && !isNotApplicable ? <button type="button" className="secondary-button appendix-b-add" onClick={() => setAdding(true)}>新增 {category.code} 记录</button> : null}
          {!records.length && !adding && !isNotApplicable ? <p className="report-empty-state">尚无结构化记录；可以先录入事实，证明图片允许后补。</p> : null}
        </div>
      ) : null}
    </section>
  );
}

type RecordProps = CardProps & {
  item?: AppendixBEvidenceItem;
  images: AppendixBEvidenceItem[];
  onCancel?: () => void;
  onMoveUp?: () => void;
  onMoveDown?: () => void;
};

function AppendixBRecordEditor(props: RecordProps) {
  const { category, item, projectRevision, projectUuid, setDirty, onReload, onError } = props;
  const baseline = useMemo(() => recordDraft(category, item), [category, item]);
  const [draft, setDraft] = useState<AppendixBRecordInput>(baseline);
  const [saving, setSaving] = useState(false);
  const dirty = JSON.stringify(draft) !== JSON.stringify(baseline);
  const token = `record:${item?.item_uuid ?? `new:${category.category_code}`}`;
  useEffect(() => {
    setDirty(token, dirty);
    return () => setDirty(token, false);
  }, [dirty, setDirty, token]);
  const relatedMembers = new Set(draft.member_uuids);
  const relatedOnsite = new Set(draft.related_item_uuids);

  function patchDraft(value: Partial<AppendixBRecordInput>) {
    setDraft((current) => ({ ...current, ...value }));
  }
  function patchMetadata(key: string, value: unknown) {
    setDraft((current) => ({ ...current, metadata: { ...current.metadata, [key]: value } }));
  }
  function toggleMember(memberUuid: string, checked: boolean, single = false) {
    patchDraft({ member_uuids: single ? (checked ? [memberUuid] : []) : checked
      ? [...draft.member_uuids, memberUuid]
      : draft.member_uuids.filter((value) => value !== memberUuid) });
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    onError(undefined);
    try {
      if (item) await updateAppendixBRecord(item, projectRevision, draft);
      else await createAppendixBRecord(projectUuid, category.category_code, projectRevision, draft);
      onReload(`${category.code} 记录已${item ? "更新" : "新增"}。`);
    } catch (saveError) {
      onError(messageOf(saveError, `${category.code} 记录保存失败。`));
    } finally {
      setSaving(false);
    }
  }
  async function remove() {
    if (!item || !window.confirm(`确定删除这条 ${category.code} 记录及其全部图片吗？`)) return;
    onError(undefined);
    try {
      await deleteAppendixBItem(item, projectRevision);
      onReload(`${category.code} 记录已删除。`);
    } catch (deleteError) {
      onError(messageOf(deleteError, `${category.code} 记录删除失败。`));
    }
  }

  const memberMode = ["travel_accommodation", "onsite_process"].includes(category.category_code)
    ? "multiple"
    : ["assessor_roster", "assessor_exam_proof"].includes(category.category_code) ? "single" : "none";
  const showStart = !["assessor_roster", "assessor_exam_proof"].includes(category.category_code);
  const showEnd = ["travel_accommodation", "onsite_process"].includes(category.category_code);
  const showOrganization = ["travel_accommodation", "onsite_process"].includes(category.category_code);
  const effectiveClient = props.organizations.find((value) => value.organization_type === "client")?.name
    || props.organizations.find((value) => value.organization_type === "assessed")?.name
    || "尚未填写被测单位";

  return (
    <article className="appendix-b-record">
      <div className="report-card-heading">
        <div><strong>{item ? item.title || RECORD_SUBTYPES[category.category_code]?.find((option) => option.value === item.subtype)?.label || "结构化记录" : `新增 ${category.code} 记录`}</strong><small>{item ? `记录 ${item.item_uuid.slice(0, 8)} · 版本 ${item.revision}` : "保存后可上传证明图片"}</small></div>
        <div className="report-inline-actions">
          {props.onMoveUp ? <button type="button" className="secondary-button" onClick={props.onMoveUp}>上移</button> : null}
          {props.onMoveDown ? <button type="button" className="secondary-button" onClick={props.onMoveDown}>下移</button> : null}
          {item ? <button type="button" className="danger-button" disabled={dirty} onClick={() => void remove()}>删除记录</button> : null}
        </div>
      </div>
      <form className="appendix-b-record-form" onSubmit={save}>
        <label><span>材料类型</span><select value={draft.subtype} onChange={(event) => patchDraft({ subtype: event.target.value })}>{RECORD_SUBTYPES[category.category_code]?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
        <label><span>标题（可选）</span><input value={draft.title} onChange={(event) => patchDraft({ title: event.target.value })} /></label>
        {showStart ? <label><span>{dateLabel(category.category_code, false)}</span><input type="date" value={draft.starts_on ?? ""} onChange={(event) => patchDraft({ starts_on: event.target.value || null })} /></label> : null}
        {showEnd ? <label><span>{dateLabel(category.category_code, true)}</span><input type="date" value={draft.ends_on ?? ""} onChange={(event) => patchDraft({ ends_on: event.target.value || null })} /></label> : null}
        {showOrganization ? <label><span>责任单位</span><select value={draft.organization_uuid ?? ""} onChange={(event) => patchDraft({ organization_uuid: event.target.value || null })}><option value="">未选择</option>{props.organizations.map((organization) => <option key={organization.organization_uuid} value={organization.organization_uuid}>{organization.name || organization.organization_type}</option>)}</select></label> : null}
        {category.category_code === "onsite_process" ? <label><span>现场地点</span><input value={draft.location} onChange={(event) => patchDraft({ location: event.target.value })} /></label> : null}
        {category.category_code === "engagement_proof" ? <>
          <label><span>有效委托单位（中央数据只读）</span><input value={effectiveClient} readOnly /></label>
          <label><span>文件类型</span><input value={metadataText(draft.metadata, "file_type")} onChange={(event) => patchMetadata("file_type", event.target.value)} /></label>
          <label><span>委托金额</span><input inputMode="decimal" value={metadataText(draft.metadata, "amount")} onChange={(event) => patchMetadata("amount", event.target.value)} /></label>
          <label><span>系统密评单价</span><input inputMode="decimal" value={metadataText(draft.metadata, "unit_price")} onChange={(event) => patchMetadata("unit_price", event.target.value)} /></label>
        </> : null}
        {category.category_code === "travel_accommodation" ? <label className="checkbox-label"><input type="checkbox" checked={draft.metadata.is_local === true} onChange={(event) => patchMetadata("is_local", event.target.checked)} />本地项目，无需差旅票证</label> : null}
        {category.category_code === "plan_review" ? <label><span>方案名称</span><input value={metadataText(draft.metadata, "plan_name")} onChange={(event) => patchMetadata("plan_name", event.target.value)} /></label> : null}
        {category.category_code === "assessor_roster" ? <label><span>人员角色</span><select value={metadataText(draft.metadata, "role") || "member"} onChange={(event) => patchMetadata("role", event.target.value)}>{ROLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label> : null}
        {category.category_code === "grading_filing" ? <FilingFields draft={draft} patchMetadata={patchMetadata} /> : null}
        {memberMode !== "none" ? (
          <fieldset className="appendix-b-check-grid"><legend>{memberMode === "multiple" ? "参与人员" : "关联人员"}</legend>{props.members.map((member) => <label key={member.member_uuid} className="checkbox-label"><input type={memberMode === "single" ? "radio" : "checkbox"} name={`${token}-member`} checked={relatedMembers.has(member.member_uuid)} onChange={(event) => toggleMember(member.member_uuid, event.target.checked, memberMode === "single")} />{member.name || "未命名成员"}<small>{member.team_role === "leader" ? "项目负责人" : "组员"}{member.qualification_passed_at ? ` · ${member.qualification_passed_at}` : " · 未填写考试通过时间"}</small></label>)}</fieldset>
        ) : null}
        {category.category_code === "travel_accommodation" ? (
          <fieldset className="appendix-b-check-grid"><legend>显式覆盖的 B-3 进场记录</legend>{props.onsiteRecords.length ? props.onsiteRecords.map((onsite) => <label key={onsite.item_uuid} className="checkbox-label"><input type="checkbox" checked={relatedOnsite.has(onsite.item_uuid)} onChange={(event) => patchDraft({ related_item_uuids: event.target.checked ? [...draft.related_item_uuids, onsite.item_uuid] : draft.related_item_uuids.filter((value) => value !== onsite.item_uuid) })} />{onsite.starts_on} 至 {onsite.ends_on}{onsite.location ? ` · ${onsite.location}` : ""}</label>) : <span>请先填写 B-3 进场记录。</span>}</fieldset>
        ) : null}
        <AppendixBImpactPreview categoryCode={category.category_code} draft={draft} item={item} onsiteRecords={props.onsiteRecords} />
        <div className="report-form-actions"><span className={dirty ? "dirty-chip" : "clean-chip"}>{dirty ? "未保存" : "已保存"}</span>{props.onCancel ? <button type="button" className="secondary-button" onClick={props.onCancel}>取消新增</button> : null}<button type="submit" disabled={saving || !dirty}>{saving ? "保存中..." : item ? "保存记录" : "新增记录"}</button></div>
      </form>
      {item ? <AppendixBImages {...props} parent={item} images={props.images} /> : null}
    </article>
  );
}

function AppendixBImpactPreview({
  categoryCode,
  draft,
  item,
  onsiteRecords
}: {
  categoryCode: string;
  draft: AppendixBRecordInput;
  item?: AppendixBEvidenceItem;
  onsiteRecords: AppendixBEvidenceItem[];
}) {
  let text = "";
  if (categoryCode === "onsite_process") {
    const other = onsiteRecords.filter((record) => record.item_uuid !== item?.item_uuid);
    const starts = [...other.map((record) => record.starts_on), draft.starts_on].filter((value): value is string => Boolean(value)).sort();
    const ends = [...other.map((record) => record.ends_on), draft.ends_on].filter((value): value is string => Boolean(value)).sort();
    text = starts.length && ends.length
      ? `保存影响预览：现场测评阶段将同步为 ${starts[0]} 至 ${ends[ends.length - 1]}。`
      : "保存影响预览：进场记录会成为现场测评阶段日期的权威来源。";
  } else if (categoryCode === "travel_accommodation") {
    text = draft.metadata.is_local === true
      ? "保存影响预览：基本信息将标记本地项目无差旅；已上传票证仍保留并提示确认。"
      : `保存影响预览：将同步差旅记录，并关联 ${draft.related_item_uuids.length} 条 B-3 进场记录。`;
  } else if (categoryCode === "plan_review") {
    text = `保存影响预览：方案评审日期将回填为 ${draft.starts_on || "空"}，且必须早于现场开始日期。`;
  } else if (categoryCode === "report_review") {
    text = `保存影响预览：基本信息表审核日期将回填为 ${draft.starts_on || "空"}。`;
  } else if (categoryCode === "assessor_roster") {
    const role = metadataText(draft.metadata, "role") || "member";
    text = ["compiler", "reviewer", "approver"].includes(role)
      ? "保存影响预览：该审批角色将同步到基本信息表；三个审批角色必须由不同人员担任。"
      : "保存影响预览：普通组员只进入表 B-7，不占用审批角色。";
  }
  return text ? <p className="appendix-b-impact">{text}</p> : null;
}

function FilingFields({ draft, patchMetadata }: { draft: AppendixBRecordInput; patchMetadata: (key: string, value: unknown) => void }) {
  const same = draft.metadata.filing_system_same;
  return <>
    <label><span>备案系统与被测系统是否相同</span><select value={same === true ? "yes" : same === false ? "no" : ""} onChange={(event) => patchMetadata("filing_system_same", event.target.value === "yes" ? true : event.target.value === "no" ? false : null)}><option value="">未选择</option><option value="yes">相同</option><option value="no">不同</option></select></label>
    <label><span>备案系统名称{same === true ? "（导出时使用被测系统名称）" : ""}</span><input disabled={same === true} value={metadataText(draft.metadata, "filing_system_name")} onChange={(event) => patchMetadata("filing_system_name", event.target.value)} /></label>
    <label className="wide"><span>差异说明</span><textarea disabled={same !== false} value={metadataText(draft.metadata, "difference")} onChange={(event) => patchMetadata("difference", event.target.value)} /></label>
  </>;
}

function dateLabel(categoryCode: string, end: boolean): string {
  if (categoryCode === "engagement_proof") return "签订日期";
  if (categoryCode === "travel_accommodation") return end ? "差旅结束日期" : "差旅开始日期";
  if (categoryCode === "onsite_process") return end ? "离场日期" : "进场日期";
  if (categoryCode === "plan_review" || categoryCode === "report_review") return "评审日期";
  if (categoryCode === "grading_filing") return "备案日期";
  return "签署日期";
}

type ImageProps = CardProps & { parent: AppendixBEvidenceItem; images: AppendixBEvidenceItem[] };

function AppendixBImages(props: ImageProps) {
  const options = IMAGE_SUBTYPES[props.category.category_code] ?? [];
  const [subtype, setSubtype] = useState(options[0]?.value ?? "");
  const [caption, setCaption] = useState("");
  const [altText, setAltText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const token = `upload:${props.parent.item_uuid}`;
  const dirty = files.length > 0 || Boolean(caption || altText);
  useEffect(() => {
    props.setDirty(token, dirty);
    return () => props.setDirty(token, false);
  }, [dirty, props.setDirty, token]);
  if (!options.length) return null;

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!files.length) return;
    setUploading(true);
    props.onError(undefined);
    try {
      await uploadAppendixBImages(props.parent.item_uuid, props.projectRevision, { subtype, caption, alt_text: altText, files });
      props.onReload(`${props.category.code} 已上传 ${files.length} 张证明图片。`);
    } catch (uploadError) {
      props.onError(messageOf(uploadError, "证明图片上传失败。"));
    } finally {
      setUploading(false);
    }
  }

  return (
    <section className="appendix-b-images">
      <h5>证明图片</h5>
      <div className="appendix-b-image-grid">{props.images.map((image) => <AppendixBImageEditor key={image.item_uuid} {...props} image={image} />)}</div>
      <form className="appendix-b-upload" onSubmit={upload}>
        <label><span>图片类型</span><select value={subtype} onChange={(event) => setSubtype(event.target.value)}>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
        <label><span>题注</span><input value={caption} onChange={(event) => setCaption(event.target.value)} placeholder="允许后补，但会产生提示" /></label>
        <label><span>替代文本</span><input value={altText} onChange={(event) => setAltText(event.target.value)} /></label>
        <label className="wide"><span>选择 PNG/JPEG（可多选，单张不超过 20 MiB）</span><input type="file" accept="image/png,image/jpeg,.png,.jpg,.jpeg" multiple onChange={(event) => setFiles(Array.from(event.target.files ?? []))} /></label>
        <div className="report-form-actions"><button type="submit" disabled={!files.length || uploading}>{uploading ? "上传中..." : `上传${files.length ? ` ${files.length} 张` : "图片"}`}</button></div>
      </form>
    </section>
  );
}

function AppendixBImageEditor(props: ImageProps & { image: AppendixBEvidenceItem }) {
  const { image } = props;
  const [subtype, setSubtype] = useState(image.subtype);
  const [caption, setCaption] = useState(image.caption);
  const [altText, setAltText] = useState(image.alt_text);
  const [sortOrder, setSortOrder] = useState(image.sort_order);
  const [replacement, setReplacement] = useState<File>();
  const [saving, setSaving] = useState(false);
  const dirty = subtype !== image.subtype || caption !== image.caption || altText !== image.alt_text || sortOrder !== image.sort_order || Boolean(replacement);
  const token = `image:${image.item_uuid}`;
  useEffect(() => {
    props.setDirty(token, dirty);
    return () => props.setDirty(token, false);
  }, [dirty, props.setDirty, token]);
  async function save() {
    setSaving(true);
    props.onError(undefined);
    try {
      let current = image;
      if (subtype !== image.subtype || caption !== image.caption || altText !== image.alt_text || sortOrder !== image.sort_order) {
        current = await updateAppendixBImage(image, props.projectRevision, { subtype, caption, alt_text: altText, sort_order: sortOrder });
      }
      if (replacement) {
        const revision = current === image ? props.projectRevision : props.projectRevision + 1;
        await replaceAppendixBImage(current, revision, replacement);
      }
      props.onReload("图片信息已保存。排序号越小越靠前。");
    } catch (saveError) {
      props.onError(messageOf(saveError, "图片保存失败。"));
    } finally {
      setSaving(false);
    }
  }
  async function remove() {
    if (!window.confirm(`确定删除图片“${image.caption || image.original_name}”吗？`)) return;
    props.onError(undefined);
    try {
      await deleteAppendixBItem(image, props.projectRevision);
      props.onReload("证明图片已删除。" );
    } catch (deleteError) {
      props.onError(messageOf(deleteError, "图片删除失败。"));
    }
  }
  return (
    <article className="appendix-b-image-card">
      <button type="button" className="appendix-b-thumbnail" onClick={() => props.onPreview(image)}><img src={resolveFileUrl(image.file_url)} alt={image.alt_text || image.caption || "证据图片"} /></button>
      <label><span>图片类型</span><select value={subtype} onChange={(event) => setSubtype(event.target.value)}>{IMAGE_SUBTYPES[props.category.category_code]?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
      <label><span>题注</span><input value={caption} onChange={(event) => setCaption(event.target.value)} /></label>
      <label><span>替代文本</span><input value={altText} onChange={(event) => setAltText(event.target.value)} /></label>
      <label><span>排序号</span><input type="number" min="0" value={sortOrder} onChange={(event) => setSortOrder(Math.max(0, Number(event.target.value) || 0))} /></label>
      <label><span>替换文件</span><input type="file" accept="image/png,image/jpeg,.png,.jpg,.jpeg" onChange={(event) => setReplacement(event.target.files?.[0])} /></label>
      <small>{image.pixel_width}×{image.pixel_height}px · {image.original_name}</small>
      <div className="report-form-actions"><button type="button" className="danger-button" disabled={dirty} onClick={() => void remove()}>删除</button><button type="button" disabled={!dirty || saving} onClick={() => void save()}>{saving ? "保存中..." : "保存图片"}</button></div>
    </article>
  );
}

function AppendixBIssues({ issues }: { issues: AppendixBIssue[] }) {
  if (!issues.length) return null;
  return <ul className="appendix-b-issues">{issues.map((issue, index) => <li key={`${issue.code}-${issue.item_uuid ?? "category"}-${index}`} className={issue.severity}><strong>{issue.severity === "error" ? "错误" : "提示"}</strong><span>{issue.message}</span><code>{issue.code}</code></li>)}</ul>;
}
