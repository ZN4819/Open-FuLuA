import { FormEvent, useEffect, useMemo, useState } from "react";

import type {
  RecordTemplateSlot,
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
};

const TEMPLATE_TYPE_ORDER: Record<RecordTemplateSlot["template_type"], number> = {
  compliant: 0,
  non_compliant: 1,
  not_applicable: 2
};

const TEMPLATE_TYPE_CLASS: Record<RecordTemplateSlot["template_type"], string> = {
  compliant: "compliant",
  non_compliant: "non-compliant",
  not_applicable: "not-applicable"
};

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

export function TemplateManagerPanel({
  profile,
  activeSectionCode,
  recordTemplateSlots,
  onClose,
  onUpdateSlot,
  onResetSlot
}: TemplateManagerPanelProps) {
  const [sectionFilter, setSectionFilter] = useState(activeSectionCode);
  const [unitFilter, setUnitFilter] = useState("");
  const [keyword, setKeyword] = useState("");
  const [drafts, setDrafts] = useState<Record<number, SlotDraft>>({});
  const [busySlotId, setBusySlotId] = useState<number | null>(null);
  const [resettingSlotId, setResettingSlotId] = useState<number | null>(null);
  const [message, setMessage] = useState<string>();
  const [error, setError] = useState<string>();

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

  const sectionSlots = useMemo(
    () =>
      recordTemplateSlots
        .filter((slot) => slot.section_code === sectionFilter)
        .sort((first, second) => {
          return (
            first.unit.localeCompare(second.unit, "zh-CN") ||
            TEMPLATE_TYPE_ORDER[first.template_type] - TEMPLATE_TYPE_ORDER[second.template_type]
          );
        }),
    [recordTemplateSlots, sectionFilter]
  );

  const unitOptions = useMemo(() => uniqueValues(sectionSlots.map((slot) => slot.unit)), [sectionSlots]);
  const customizedCount = sectionSlots.filter((slot) => slot.is_customized).length;

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
      visibleUnits.map((unit) => ({
        unit,
        slots: sectionSlots
          .filter((slot) => slot.unit === unit)
          .sort((first, second) => TEMPLATE_TYPE_ORDER[first.template_type] - TEMPLATE_TYPE_ORDER[second.template_type])
      })),
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
      setMessage(`${saved.unit} 的${saved.template_type_label}已保存。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存三类模板失败。");
    } finally {
      setBusySlotId(null);
    }
  }

  async function handleResetSlot(slot: RecordTemplateSlot) {
    const confirmed = window.confirm(`确定将“${slot.unit} / ${slot.template_type_label}”恢复为默认模板吗？当前修改会被覆盖。`);
    if (!confirmed) {
      return;
    }

    setResettingSlotId(slot.id);
    setError(undefined);
    setMessage(undefined);
    try {
      const reset = await onResetSlot(slot.id);
      setDrafts((current) => ({ ...current, [reset.id]: draftFromSlot(reset) }));
      setMessage(`${reset.unit} 的${reset.template_type_label}已恢复默认。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "恢复默认模板失败。");
    } finally {
      setResettingSlotId(null);
    }
  }

  return (
    <section className="feedback-panel template-manager-panel" aria-label="三类结果记录模板管理">
      <div className="feedback-heading template-manager-heading">
        <div>
          <p className="eyebrow">三类模板</p>
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

      <div className="template-slot-board">
        <div className="template-list-heading">
          <div>
            <p className="eyebrow">固定槽位</p>
            <h4>{visibleUnits.length} 个测评单元</h4>
          </div>
          <span className="status-chip">每个单元 3 类模板</span>
        </div>

        {unitGroups.length === 0 ? (
          <p className="template-empty">没有匹配的三类模板，可调整章节、测评单元或关键词。</p>
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

                <div className="template-slot-grid">
                  {group.slots.map((slot) => {
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
                          <span>结果记录正文</span>
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
                  })}
                </div>
              </article>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
