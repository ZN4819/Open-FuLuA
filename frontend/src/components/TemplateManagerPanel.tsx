import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";

import type {
  RecordTemplateSlot,
  RecordTemplateSlotGroup,
  RecordTemplateSlotExport,
  RecordTemplateSlotImportPayload,
  RecordTemplateSlotImportResult,
  RecordTemplateSlotUpdateInput,
  TemplateProfile
} from "../api/client";

type SlotDraft = {
  title: string;
  record_text: string;
  tags: string;
};

type TemplateManagerPanelProps = {
  profile: TemplateProfile;
  activeSectionCode: string;
  recordTemplateSlots: RecordTemplateSlot[];
  onClose: () => void;
  onUpdateSlot: (slotId: number, payload: RecordTemplateSlotUpdateInput) => Promise<RecordTemplateSlot>;
  onResetSlot: (slotId: number) => Promise<RecordTemplateSlot>;
  onExportSlots: () => Promise<RecordTemplateSlotExport>;
  onPreviewImportSlots: (payload: RecordTemplateSlotImportPayload) => Promise<RecordTemplateSlotImportResult>;
  onImportSlots: (payload: RecordTemplateSlotImportPayload) => Promise<RecordTemplateSlotImportResult>;
};

const TEMPLATE_GROUP_ORDER: Record<RecordTemplateSlotGroup, number> = {
  verification_record: 0,
  score_basis: 1
};

const TEMPLATE_GROUP_FALLBACK_LABELS: Record<RecordTemplateSlotGroup, string> = {
  verification_record: "测评验证记录模板",
  score_basis: "测评对象评分计算依据模板"
};
const TECHNICAL_SCORE_BASIS_SECTION_CODE = "TECHNICAL";
const SCORE_BASIS_TEMPLATE_TYPES: RecordTemplateSlot["template_type"][] = ["fully_compliant", "score_adjusted", "non_compliant"];

const TEMPLATE_TYPE_ORDER: Record<RecordTemplateSlotGroup, Partial<Record<RecordTemplateSlot["template_type"], number>>> = {
  verification_record: {
    compliant: 0,
    non_compliant: 1,
    not_applicable: 2
  },
  score_basis: {
    fully_compliant: 0,
    score_adjusted: 1,
    non_compliant: 2
  }
};

const TEMPLATE_TYPE_CLASS: Record<RecordTemplateSlot["template_type"], string> = {
  compliant: "compliant",
  fully_compliant: "compliant",
  score_adjusted: "score-adjusted",
  non_compliant: "non-compliant",
  not_applicable: "not-applicable"
};

function templateSlotSortValue(slot: RecordTemplateSlot) {
  return TEMPLATE_TYPE_ORDER[slot.template_group]?.[slot.template_type] ?? 99;
}

const TEMPLATE_GROUPS = Object.keys(TEMPLATE_GROUP_ORDER) as RecordTemplateSlotGroup[];

function slotsByTemplateGroup(slots: RecordTemplateSlot[]) {
  return TEMPLATE_GROUPS.map((templateGroup) => {
    const groupSlots = slots
      .filter((slot) => slot.template_group === templateGroup)
      .sort((first, second) => templateSlotSortValue(first) - templateSlotSortValue(second));
    return {
      templateGroup,
      label: groupSlots[0]?.template_group_label ?? TEMPLATE_GROUP_FALLBACK_LABELS[templateGroup],
      slots: groupSlots
    };
  }).filter((group) => group.slots.length > 0);
}

function dedupeSharedScoreBasisSlots(slots: RecordTemplateSlot[]) {
  const preferredSlots = slots
    .filter((slot) => slot.template_group === "score_basis")
    .sort((first, second) => {
      const firstIsGlobal = first.section_code === TECHNICAL_SCORE_BASIS_SECTION_CODE;
      const secondIsGlobal = second.section_code === TECHNICAL_SCORE_BASIS_SECTION_CODE;
      if (firstIsGlobal !== secondIsGlobal) {
        return firstIsGlobal ? -1 : 1;
      }
      if (first.is_customized !== second.is_customized) {
        return first.is_customized ? -1 : 1;
      }
      return templateSlotSortValue(first) - templateSlotSortValue(second) || first.id - second.id;
    });
  const slotByType = new Map<RecordTemplateSlot["template_type"], RecordTemplateSlot>();
  preferredSlots.forEach((slot) => {
    if (!slotByType.has(slot.template_type)) {
      slotByType.set(slot.template_type, slot);
    }
  });
  return SCORE_BASIS_TEMPLATE_TYPES
    .map((templateType) => slotByType.get(templateType))
    .filter((slot): slot is RecordTemplateSlot => Boolean(slot));
}

function tagsToText(tags?: string[]) {
  return tags?.join("，") ?? "";
}

function tagsFromText(value: string) {
  return value
    .split(/[，,;；\n]/)
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function uniqueValues(values: string[]) {
  const seen = new Set<string>();
  const result: string[] = [];
  values.forEach((value) => {
    const normalized = value.trim();
    if (normalized && !seen.has(normalized)) {
      seen.add(normalized);
      result.push(normalized);
    }
  });
  return result;
}

function draftFromSlot(slot: RecordTemplateSlot): SlotDraft {
  return {
    title: slot.title,
    record_text: slot.record_text,
    tags: tagsToText(slot.tags)
  };
}

function normalizedDraft(draft: SlotDraft) {
  return {
    title: draft.title.trim(),
    record_text: draft.record_text.trim(),
    tags: tagsFromText(draft.tags).join("，")
  };
}

function normalizedSlot(slot: RecordTemplateSlot) {
  return {
    title: slot.title.trim(),
    record_text: slot.record_text.trim(),
    tags: tagsToText(slot.tags)
  };
}

function slotDraftIsDirty(slot: RecordTemplateSlot, draft: SlotDraft) {
  const current = normalizedDraft(draft);
  const saved = normalizedSlot(slot);
  return current.title !== saved.title || current.record_text !== saved.record_text || current.tags !== saved.tags;
}

function slotMatchesKeyword(slot: RecordTemplateSlot, draft: SlotDraft, keyword: string) {
  if (!keyword.trim()) {
    return true;
  }
  const content = [
    slot.section_code,
    slot.unit,
    slot.template_group_label,
    slot.template_type_label,
    draft.title,
    draft.record_text,
    draft.tags
  ]
    .join(" ")
    .toLowerCase();
  return content.includes(keyword.trim().toLowerCase());
}

function formatTemplateDate(value?: string | null) {
  if (!value) {
    return "暂无时间";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}

function sectionLabel(profile: TemplateProfile, sectionCode: string) {
  const section = profile.sections.find((item) => item.code === sectionCode);
  return section ? `${section.code} ${section.title}` : sectionCode;
}

function downloadJsonFile(data: unknown, fileName: string) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function importActionLabel(action: RecordTemplateSlotImportResult["items"][number]["action"]) {
  if (action === "update") {
    return "更新";
  }
  if (action === "error") {
    return "错误";
  }
  if (action === "create") {
    return "新增";
  }
  return "跳过";
}

export function TemplateManagerPanel({
  profile,
  activeSectionCode,
  recordTemplateSlots,
  onClose,
  onUpdateSlot,
  onResetSlot,
  onExportSlots,
  onPreviewImportSlots,
  onImportSlots
}: TemplateManagerPanelProps) {
  const [sectionFilter, setSectionFilter] = useState(activeSectionCode);
  const [unitFilter, setUnitFilter] = useState("");
  const [keyword, setKeyword] = useState("");
  const [drafts, setDrafts] = useState<Record<number, SlotDraft>>({});
  const [busySlotId, setBusySlotId] = useState<number | null>(null);
  const [resettingSlotId, setResettingSlotId] = useState<number | null>(null);
  const [message, setMessage] = useState<string>();
  const [error, setError] = useState<string>();
  const [importPayload, setImportPayload] = useState<RecordTemplateSlotImportPayload>();
  const [importFileName, setImportFileName] = useState<string>();
  const [importPreview, setImportPreview] = useState<RecordTemplateSlotImportResult>();
  const [isExportingConfig, setIsExportingConfig] = useState(false);
  const [isPreviewingImport, setIsPreviewingImport] = useState(false);
  const [isImportingConfig, setIsImportingConfig] = useState(false);

  useEffect(() => {
    setSectionFilter(activeSectionCode);
    setUnitFilter("");
    setKeyword("");
    setMessage(undefined);
    setError(undefined);
  }, [activeSectionCode]);

  useEffect(() => {
    setDrafts(Object.fromEntries(recordTemplateSlots.map((slot) => [slot.id, draftFromSlot(slot)])));
  }, [recordTemplateSlots]);

  const sharedScoreBasisSlots = useMemo(
    () => dedupeSharedScoreBasisSlots(recordTemplateSlots),
    [recordTemplateSlots]
  );

  const sectionVerificationSlots = useMemo(
    () =>
      recordTemplateSlots
        .filter((slot) => slot.section_code === sectionFilter && slot.template_group === "verification_record")
        .sort((first, second) => {
          return (
            first.unit.localeCompare(second.unit, "zh-CN") ||
            templateSlotSortValue(first) - templateSlotSortValue(second)
          );
        }),
    [recordTemplateSlots, sectionFilter]
  );

  const sectionSlots = sectionVerificationSlots;
  const unitOptions = useMemo(() => uniqueValues(sectionSlots.map((slot) => slot.unit)), [sectionSlots]);
  const customizedCount = [...sectionSlots, ...sharedScoreBasisSlots].filter((slot) => slot.is_customized).length;

  const visibleUnits = useMemo(() => {
    return unitOptions.filter((unit) => {
      if (unitFilter && unit !== unitFilter) {
        return false;
      }
      const slotsForUnit = sectionSlots.filter((slot) => slot.unit === unit);
      return slotsForUnit.some((slot) => slotMatchesKeyword(slot, drafts[slot.id] ?? draftFromSlot(slot), keyword));
    });
  }, [drafts, keyword, sectionSlots, unitFilter, unitOptions]);

  const unitGroups = useMemo(
    () =>
      visibleUnits.map((unit) => {
        const slots = sectionSlots
          .filter((slot) => slot.unit === unit)
          .sort(
            (first, second) =>
              TEMPLATE_GROUP_ORDER[first.template_group] - TEMPLATE_GROUP_ORDER[second.template_group] ||
              templateSlotSortValue(first) - templateSlotSortValue(second)
          );
        return {
          unit,
          slots,
          slotGroups: slotsByTemplateGroup(slots)
        };
      }),
    [sectionSlots, visibleUnits]
  );

  function updateDraft(slotId: number, patch: Partial<SlotDraft>) {
    setDrafts((current) => ({
      ...current,
      [slotId]: {
        ...(current[slotId] ?? { title: "", record_text: "", tags: "" }),
        ...patch
      }
    }));
    setMessage(undefined);
    setError(undefined);
  }

  async function handleSaveSlot(event: FormEvent<HTMLFormElement>, slot: RecordTemplateSlot) {
    event.preventDefault();
    const draft = drafts[slot.id] ?? draftFromSlot(slot);
    const normalized = normalizedDraft(draft);
    if (!normalized.record_text) {
      setError("模板正文不能为空。");
      return;
    }

    setBusySlotId(slot.id);
    setError(undefined);
    setMessage(undefined);
    try {
      const saved = await onUpdateSlot(slot.id, {
        title: normalized.title || slot.template_type_label,
        record_text: normalized.record_text,
        tags: tagsFromText(draft.tags)
      });
      setDrafts((current) => ({ ...current, [saved.id]: draftFromSlot(saved) }));
      setMessage(`${saved.unit} 的${saved.template_group_label} / ${saved.template_type_label}已保存。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存分段模板失败。");
    } finally {
      setBusySlotId(null);
    }
  }

  async function handleResetSlot(slot: RecordTemplateSlot) {
    const confirmed = window.confirm(`确定将“${slot.unit} / ${slot.template_group_label} / ${slot.template_type_label}”恢复为默认模板吗？当前修改会被覆盖。`);
    if (!confirmed) {
      return;
    }

    setResettingSlotId(slot.id);
    setError(undefined);
    setMessage(undefined);
    try {
      const reset = await onResetSlot(slot.id);
      setDrafts((current) => ({ ...current, [reset.id]: draftFromSlot(reset) }));
      setMessage(`${reset.unit} 的${reset.template_group_label} / ${reset.template_type_label}已恢复默认。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "恢复默认分段模板失败。");
    } finally {
      setResettingSlotId(null);
    }
  }

  async function handleExportTemplateSlots() {
    setIsExportingConfig(true);
    setError(undefined);
    setMessage(undefined);
    try {
      const exported = await onExportSlots();
      downloadJsonFile(exported, `fulua-record-template-slots-${new Date().toISOString().slice(0, 10)}.json`);
      setMessage(`已导出 ${exported.templates.length} 条分段模板配置。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出分段模板配置失败。");
    } finally {
      setIsExportingConfig(false);
    }
  }

  async function handleImportFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) {
      return;
    }

    setError(undefined);
    setMessage(undefined);
    setImportPreview(undefined);
    try {
      const parsed = JSON.parse(await file.text()) as RecordTemplateSlotImportPayload;
      if (!Array.isArray(parsed.templates)) {
        throw new Error("导入文件必须包含 templates 列表。");
      }
      setImportPayload(parsed);
      setImportFileName(file.name);
      setMessage(`已读取 ${file.name}，请先预览导入结果。`);
    } catch (err) {
      setImportPayload(undefined);
      setImportFileName(undefined);
      setError(err instanceof Error ? err.message : "读取分段模板配置文件失败。");
    }
  }

  async function handlePreviewImport() {
    if (!importPayload) {
      setError("请先选择分段模板配置 JSON 文件。");
      return;
    }

    setIsPreviewingImport(true);
    setError(undefined);
    setMessage(undefined);
    try {
      const result = await onPreviewImportSlots(importPayload);
      setImportPreview(result);
      setMessage("导入预览已生成。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成导入预览失败。");
    } finally {
      setIsPreviewingImport(false);
    }
  }

  async function handleImportTemplateSlots() {
    if (!importPayload) {
      setError("请先选择分段模板配置 JSON 文件。");
      return;
    }
    if (importPreview?.summary.errors) {
      setError("导入预览仍有错误，请修正文件后再导入。");
      return;
    }

    setIsImportingConfig(true);
    setError(undefined);
    setMessage(undefined);
    try {
      const result = await onImportSlots(importPayload);
      setImportPreview(result);
      setMessage(`导入完成：更新 ${result.summary.updated}，跳过 ${result.summary.skipped}。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "导入分段模板配置失败。");
    } finally {
      setIsImportingConfig(false);
    }
  }

  function renderTemplateSlotCard(slot: RecordTemplateSlot) {
    const draft = drafts[slot.id] ?? draftFromSlot(slot);
    const isDirty = slotDraftIsDirty(slot, draft);
    const isSaving = busySlotId === slot.id;
    const isResetting = resettingSlotId === slot.id;
    return (
      <form
        className={`template-slot-card ${TEMPLATE_TYPE_CLASS[slot.template_type]}`}
        key={slot.id}
        onSubmit={(event) => handleSaveSlot(event, slot)}
      >
        <div className="template-slot-heading">
          <div>
            <p className="eyebrow">{slot.template_type_label}</p>
            <h5>{draft.title.trim() || slot.template_type_label}</h5>
          </div>
          <span className={slot.is_customized ? "dirty-chip" : "clean-chip"}>
            {slot.is_customized ? "已修改" : "默认"}
          </span>
        </div>

        <label>
          <span>标题</span>
          <input
            value={draft.title}
            onChange={(event) => updateDraft(slot.id, { title: event.target.value })}
            maxLength={500}
            placeholder={slot.template_type_label}
          />
        </label>
        <label>
          <span>{slot.template_group === "score_basis" ? "评分依据正文" : "验证记录正文"}</span>
          <textarea
            value={draft.record_text}
            onChange={(event) => updateDraft(slot.id, { record_text: event.target.value })}
            rows={8}
            required
          />
        </label>
        <label>
          <span>标签</span>
          <input
            value={draft.tags}
            onChange={(event) => updateDraft(slot.id, { tags: event.target.value })}
            placeholder="多个标签用逗号分隔"
          />
        </label>

        <div className="template-slot-meta">
          <span>更新 {formatTemplateDate(slot.updated_at)}</span>
          {isDirty ? <span>有未保存修改</span> : null}
        </div>

        <div className="template-slot-actions">
          <button type="submit" disabled={isSaving || isResetting || !isDirty || !draft.record_text.trim()}>
            {isSaving ? "保存中..." : "保存"}
          </button>
          <button
            type="button"
            className="secondary-button"
            onClick={() => handleResetSlot(slot)}
            disabled={isSaving || isResetting}
          >
            {isResetting ? "恢复中..." : "恢复默认"}
          </button>
        </div>
      </form>
    );
  }

  return (
    <section className="feedback-panel template-manager-panel" aria-label="分段结果记录模板管理">
      <div className="feedback-heading template-manager-heading">
        <div>
          <p className="eyebrow">分段模板</p>
          <h3>结果记录模板管理</h3>
        </div>
        <div className="template-manager-actions">
          <span className="status-chip">当前 {sectionLabel(profile, sectionFilter)}</span>
          <span className="status-chip">单元 {unitOptions.length}</span>
          <span className="status-chip">已修改 {customizedCount}</span>
          <button type="button" className="secondary-button" onClick={onClose}>
            收起
          </button>
        </div>
      </div>

      <div className="template-manager-tools">
        <label>
          <span>章节</span>
          <select
            value={sectionFilter}
            onChange={(event) => {
              setSectionFilter(event.target.value);
              setUnitFilter("");
              setMessage(undefined);
              setError(undefined);
            }}
          >
            {profile.sections.map((section) => (
              <option key={section.code} value={section.code}>
                {section.code} {section.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>测评单元</span>
          <select value={unitFilter} onChange={(event) => setUnitFilter(event.target.value)}>
            <option value="">全部单元</option>
            {unitOptions.map((unit) => (
              <option key={unit} value={unit}>
                {unit}
              </option>
            ))}
          </select>
        </label>
        <label className="template-search-field">
          <span>搜索</span>
          <input
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            placeholder="搜索单元、标题、正文或标签"
          />
        </label>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {message ? <p className="success">{message}</p> : null}

      <div className="template-side-block template-backup-block">
        <div className="template-side-heading">
          <div>
            <p className="eyebrow">备份与恢复</p>
            <h4>分段模板配置导入导出</h4>
          </div>
          <span className="status-chip">JSON</span>
        </div>
        <div className="template-backup-actions">
          <button type="button" className="secondary-button" onClick={handleExportTemplateSlots} disabled={isExportingConfig}>
            {isExportingConfig ? "导出中..." : "导出模板配置"}
          </button>
          <label className="secondary-button template-file-button">
            选择配置 JSON
            <input type="file" accept="application/json,.json" onChange={handleImportFileChange} />
          </label>
          <button type="button" className="secondary-button" onClick={handlePreviewImport} disabled={!importPayload || isPreviewingImport}>
            {isPreviewingImport ? "预览中..." : "预览导入"}
          </button>
          <button type="button" onClick={handleImportTemplateSlots} disabled={!importPayload || isImportingConfig || Boolean(importPreview?.summary.errors)}>
            {isImportingConfig ? "导入中..." : "确认导入"}
          </button>
        </div>
        {importFileName ? <p className="template-import-file">当前文件：{importFileName}</p> : null}
        {importPreview ? (
          <div className="template-import-preview">
            <div className="template-import-summary">
              <span className="status-chip">更新 {importPreview.summary.updated}</span>
              <span className="status-chip">跳过 {importPreview.summary.skipped}</span>
              <span className={importPreview.summary.errors > 0 ? "dirty-chip" : "clean-chip"}>错误 {importPreview.summary.errors}</span>
            </div>
            <div className="template-import-list">
              {importPreview.items.map((item) => (
                <div className={`template-import-item ${item.action}`} key={`${item.index}-${item.action}-${item.unit}-${item.template_type}`}>
                  <span className={`import-action ${item.action}`}>{importActionLabel(item.action)}</span>
                  <div>
                    <strong>{item.title || item.unit || `第 ${item.index} 条`}</strong>
                    <p>{item.message}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>

      <div className="template-slot-board">
        <div className="template-list-heading">
          <div>
            <p className="eyebrow">固定槽位</p>
            <h4>{visibleUnits.length} 个测评单元</h4>
          </div>
          <span className="status-chip">每个单元 1 组 / 3 模板</span>
        </div>

        {unitGroups.length === 0 ? (
          <p className="template-empty">没有匹配的分段模板，可调整章节、测评单元或关键词。</p>
        ) : (
          <div className="template-unit-list">
            {unitGroups.map((group) => (
              <article className="template-unit-card" key={`${sectionFilter}-${group.unit}`}>
                <div className="template-unit-heading">
                  <div>
                    <p className="eyebrow">测评单元</p>
                    <h4>{group.unit}</h4>
                  </div>
                  <div className="template-unit-meta">
                    <span className="status-chip">模板 {group.slots.length}</span>
                    <span className="status-chip">已修改 {group.slots.filter((slot) => slot.is_customized).length}</span>
                  </div>
                </div>

                {group.slotGroups.map((slotGroup) => (
                  <div className="template-slot-group" key={`${group.unit}-${slotGroup.templateGroup}`}>
                    <div className="template-slot-group-heading">
                      <strong>{slotGroup.label}</strong>
                      <span className="status-chip">模板 {slotGroup.slots.length}</span>
                    </div>
                    <div className="template-slot-grid">
                      {slotGroup.slots.map((slot) => renderTemplateSlotCard(slot))}
                    </div>
                  </div>
                ))}
              </article>
            ))}
            {sharedScoreBasisSlots.length > 0 ? (
              <article className="template-unit-card template-slot-global-card" key="technical-score-basis-global">
                <div className="template-unit-heading">
                  <div>
                    <p className="eyebrow">A-1 至 A-4 通用</p>
                    <h4>测评对象评分计算依据模板</h4>
                  </div>
                  <div className="template-unit-meta">
                    <span className="status-chip">模板 {sharedScoreBasisSlots.length}</span>
                    <span className="status-chip">已修改 {sharedScoreBasisSlots.filter((slot) => slot.is_customized).length}</span>
                  </div>
                </div>
                <div className="template-slot-group">
                  <div className="template-slot-group-heading">
                    <strong>{sharedScoreBasisSlots[0]?.template_group_label ?? TEMPLATE_GROUP_FALLBACK_LABELS.score_basis}</strong>
                    <span className="status-chip">所有技术章节共用</span>
                  </div>
                  <div className="template-slot-grid">
                    {sharedScoreBasisSlots.map((slot) => renderTemplateSlotCard(slot))}
                  </div>
                </div>
              </article>
            ) : null}
          </div>
        )}
      </div>
    </section>
  );
}
