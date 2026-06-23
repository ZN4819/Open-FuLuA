import { useRef } from "react";

import type { AssessmentRowInput, EvidenceImage, RecordTemplateSlot, TemplateProfile } from "../api/client";

type AssessmentTableProps = {
  sectionCode: string;
  rows: AssessmentRowInput[];
  profile: TemplateProfile;
  isSaving: boolean;
  isDirty: boolean;
  evidenceImages: EvidenceImage[];
  recordTemplateSlots: RecordTemplateSlot[];
  onRowsChange: (rows: AssessmentRowInput[]) => void;
  onSave: () => void;
};

const EMPTY_METRIC = {
  d: undefined,
  a: undefined,
  k: undefined,
  object_score: "",
  unit_score: "",
  compliance: undefined
};
const FIGURE_PLACEHOLDER = "[插入图片引用]";

type TextSelection = {
  start: number;
  end: number;
};

function isTechnicalSection(code: string) {
  return ["A-1", "A-2", "A-3", "A-4"].includes(code);
}

function createEmptyRow(sortOrder: number, unit: string): AssessmentRowInput {
  return {
    unit,
    object_name: "",
    record_text: "",
    sort_order: sortOrder,
    metric_result: { ...EMPTY_METRIC },
    cross_references: []
  };
}

function uniqueValues(values: string[]) {
  const result: string[] = [];
  values.forEach((value) => {
    const trimmed = value.trim();
    if (trimmed && !result.includes(trimmed)) {
      result.push(trimmed);
    }
  });
  return result;
}

const SCORE_EXCLUDED_VALUE = "/";

function scoreText(value: string | null | undefined) {
  return (value ?? "").trim();
}

function formatScoreToFourDecimals(value: string | null | undefined) {
  const text = scoreText(value);
  if (!text || text === SCORE_EXCLUDED_VALUE) {
    return text;
  }
  const score = Number(text);
  if (!Number.isFinite(score)) {
    return text;
  }
  return score.toFixed(4);
}

function calculateTechnicalUnitScore(rows: AssessmentRowInput[]) {
  const numericScores: number[] = [];
  let filledScores = 0;
  let excludedScores = 0;

  rows.forEach((row) => {
    const score = scoreText(row.metric_result?.object_score);
    if (!score) {
      return;
    }
    filledScores += 1;
    if (score === SCORE_EXCLUDED_VALUE) {
      excludedScores += 1;
      return;
    }
    const numericScore = Number(score);
    if (Number.isFinite(numericScore)) {
      numericScores.push(numericScore);
    }
  });

  if (numericScores.length > 0) {
    const total = numericScores.reduce((sum, score) => sum + score, 0);
    return (total / numericScores.length).toFixed(4);
  }
  if (rows.length > 0 && filledScores === rows.length && excludedScores === rows.length) {
    return SCORE_EXCLUDED_VALUE;
  }
  return "";
}

function applyCalculatedUnitScores(rows: AssessmentRowInput[], shouldCalculate: boolean) {
  if (!shouldCalculate) {
    return rows;
  }

  const rowsByUnit = new Map<string, AssessmentRowInput[]>();
  rows.forEach((row) => {
    const unit = row.unit.trim();
    rowsByUnit.set(unit, [...(rowsByUnit.get(unit) ?? []), row]);
  });

  const scoreByUnit = new Map<string, string>();
  rowsByUnit.forEach((unitRows, unit) => {
    scoreByUnit.set(unit, calculateTechnicalUnitScore(unitRows));
  });

  return rows.map((row) => ({
    ...row,
    metric_result: {
      ...(row.metric_result ?? EMPTY_METRIC),
      unit_score: scoreByUnit.get(row.unit.trim()) ?? ""
    }
  }));
}
function fixedUnitsFromSlots(recordTemplateSlots: RecordTemplateSlot[], rows: AssessmentRowInput[]) {
  return uniqueValues([
    ...recordTemplateSlots.map((slot) => slot.unit),
    ...rows.map((row) => row.unit)
  ]);
}

const TEMPLATE_SLOT_ORDER: Record<RecordTemplateSlot["template_type"], number> = {
  compliant: 0,
  non_compliant: 1,
  not_applicable: 2
};

function templateSlotOptionLabel(slot: RecordTemplateSlot) {
  const label = slot.template_type_label.trim();
  const title = slot.title.trim();
  if (title && label && title !== label) {
    return `${label} - ${title}`;
  }
  return title || label || "未命名模板";
}

function templateSlotsForUnit(recordTemplateSlots: RecordTemplateSlot[], unit: string) {
  return recordTemplateSlots
    .filter((slot) => slot.unit === unit)
    .sort((first, second) => TEMPLATE_SLOT_ORDER[first.template_type] - TEMPLATE_SLOT_ORDER[second.template_type]);
}
function normalizeRows(rows: AssessmentRowInput[], unitOrder: string[] = [], calculateUnitScores = false) {
  const order = new Map(unitOrder.map((unit, index) => [unit, index]));
  const normalizedRows = rows
    .map((row, index) => {
      const unit = row.unit.trim();
      return {
        row: {
          ...row,
          unit,
          sort_order: index + 1,
          metric_result: row.metric_result ?? { ...EMPTY_METRIC },
          cross_references: row.cross_references ?? []
        },
        index
      };
    })
    .sort((first, second) => {
      const firstOrder = order.get(first.row.unit) ?? unitOrder.length;
      const secondOrder = order.get(second.row.unit) ?? unitOrder.length;
      return firstOrder - secondOrder || first.index - second.index;
    })
    .map(({ row }, index) => ({
      ...row,
      sort_order: index + 1
    }));

  return applyCalculatedUnitScores(normalizedRows, calculateUnitScores);
}

export function AssessmentTable({
  sectionCode,
  rows,
  profile,
  isSaving,
  isDirty,
  evidenceImages,
  recordTemplateSlots,
  onRowsChange,
  onSave
}: AssessmentTableProps) {
  const technical = isTechnicalSection(sectionCode);
  const metricOptions = profile.content_controls.technical_metric.options;
  const complianceOptions = profile.content_controls.management_compliance.options;
  const recordSelections = useRef<Record<number, TextSelection>>({});
  const tableTitle = technical ? "D / A / K 指标录入" : "符合情况录入";
  const unitOrder = fixedUnitsFromSlots(recordTemplateSlots, rows);
  const normalizedRows = normalizeRows(rows, unitOrder, technical);
  const templateSlotCount = recordTemplateSlots.length;
  const templateTypeCount = uniqueValues(recordTemplateSlots.map((slot) => slot.template_type)).length;
  const groupedRows = unitOrder.map((unit) => {
    const entries = normalizedRows
      .map((row, index) => ({ row, index }))
      .filter((entry) => entry.row.unit.trim() === unit);
    const unitScore = technical ? calculateTechnicalUnitScore(entries.map((entry) => entry.row)) : scoreText(entries[0]?.row.metric_result?.unit_score);
    return { unit, entries, unitScore };
  });
  const tableColumnCount = technical ? 9 : 6;

  function updateRow(index: number, patch: Partial<AssessmentRowInput>) {
    const next = normalizeRows(
      normalizedRows.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row)),
      unitOrder,
      technical
    );
    onRowsChange(next);
  }

  function updateMetric(index: number, key: string, value: string) {
    const row = normalizedRows[index];
    updateRow(index, {
      metric_result: {
        ...(row.metric_result ?? EMPTY_METRIC),
        [key]: value
      }
    });
  }

  function formatObjectScore(index: number) {
    const value = normalizedRows[index]?.metric_result?.object_score;
    updateMetric(index, "object_score", formatScoreToFourDecimals(value));
  }

  function addRow(unit: string) {
    onRowsChange(normalizeRows([...normalizedRows, createEmptyRow(normalizedRows.length + 1, unit)], unitOrder, technical));
  }

  function removeRow(index: number) {
    onRowsChange(normalizeRows(normalizedRows.filter((_, rowIndex) => rowIndex !== index), unitOrder, technical));
  }

  function updateUnitScoreForUnit(unit: string, value: string) {
    const next = normalizeRows(
      normalizedRows.map((row) => {
        if (row.unit.trim() !== unit) {
          return row;
        }
        return {
          ...row,
          metric_result: {
            ...(row.metric_result ?? EMPTY_METRIC),
            unit_score: value
          }
        };
      }),
      unitOrder,
      false
    );
    onRowsChange(next);
  }

  function rememberRecordSelection(index: number, target: HTMLTextAreaElement) {
    recordSelections.current[index] = {
      start: target.selectionStart,
      end: target.selectionEnd
    };
  }

  function insertReferenceToken(index: number, imageIdValue: string) {
    const imageId = Number(imageIdValue);
    const image = evidenceImages.find((item) => item.id === imageId);
    if (!image) {
      return;
    }
    const token = `[[FIG:${image.id}]]`;
    const displayText = image.figure_label ??
      `${profile.sections.find((section) => section.code === sectionCode)?.figure_prefix ?? `图${sectionCode}-`}${image.sort_order}`;
    const row = normalizedRows[index];
    const recordText = insertAtSelectionOrPlaceholder(row.record_text, token, recordSelections.current[index]);
    updateRow(index, {
      record_text: recordText,
      cross_references: [
        ...(row.cross_references ?? []),
        {
          target_image_id: image.id,
          token,
          display_text: displayText
        }
      ]
    });
  }

  function applyRecordTemplate(index: number, slotId: string) {
    const row = normalizedRows[index];
    const selectedSlotId = Number(slotId);
    const slot = recordTemplateSlots.find((item) => item.id === selectedSlotId && item.unit === row.unit);
    if (!slot) {
      return;
    }

    updateRow(index, {
      record_text: slot.record_text
    });
  }

  return (
    <div className="editor-block">
      <div className="editor-toolbar">
        <div className="editor-toolbar-main">
          <p className="eyebrow">{technical ? "技术测评表" : "管理测评表"}</p>
          <h3>{tableTitle}</h3>
          <div className="editor-toolbar-meta">
            <span className="status-chip">测评对象 {rows.length}</span>
            <span className="status-chip">固定单元 {unitOrder.length}</span>
            <span className="status-chip">三类模板 {templateTypeCount}</span>
            <span className="status-chip">模板槽位 {templateSlotCount}</span>
            <span className="status-chip">证据 {evidenceImages.length}</span>
          </div>
        </div>
        <div className="toolbar-actions">
          {isDirty ? <span className="dirty-chip">有未保存修改</span> : <span className="clean-chip">已保存</span>}
          <button type="button" onClick={onSave} disabled={isSaving || !isDirty}>
            {isSaving ? "保存中..." : "保存"}
          </button>
        </div>
      </div>

      {unitOrder.length === 0 ? (
        <div className="empty-table">
          <p>当前章节还没有可用的固定测评单元，请确认结果记录模板是否已加载。</p>
        </div>
      ) : (
        <div className="table-scroll">
          <table className={`assessment-table ${technical ? "technical-table" : "management-table"}`}>
            <colgroup>
              <col className="col-unit" />
              <col className="col-object" />
              <col className="col-record" />
              {technical ? (
                <>
                  <col className="col-metric" />
                  <col className="col-metric" />
                  <col className="col-metric" />
                  <col className="col-score" />
                  <col className="col-score" />
                </>
              ) : (
                <>
                  <col className="col-compliance" />
                  <col className="col-score" />
                </>
              )}
              <col className="col-action" />
            </colgroup>
            <thead>
              <tr>
                <th>测评单元</th>
                <th>测评对象</th>
                <th>结果记录</th>
                {technical ? (
                  <>
                    <th>D</th>
                    <th>A</th>
                    <th>K</th>
                    <th>对象评分</th>
                    <th>单元得分</th>
                  </>
                ) : (
                  <>
                    <th>符合情况</th>
                    <th>单元得分</th>
                  </>
                )}
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {groupedRows.map((group) =>
                group.entries.length === 0 ? (
                  <tr className="unit-empty-row" key={`${sectionCode}-${group.unit}-empty`}>
                    <td className="unit-cell fixed-unit-cell">
                      <div className="fixed-unit-content">
                        <strong>{group.unit}</strong>
                        <span>对象 0</span>
                        <button type="button" className="unit-add-button" onClick={() => addRow(group.unit)}>
                          新增对象
                        </button>
                      </div>
                    </td>
                    <td className="unit-empty-cell" colSpan={tableColumnCount - 1}>
                      当前测评单元还没有测评对象。
                    </td>
                  </tr>
                ) : (
                  group.entries.map(({ row, index }, entryIndex) => {
                    const templateSlots = templateSlotsForUnit(recordTemplateSlots, row.unit);
                    const templateOptionsCount = templateSlots.length;
                    return (
                      <tr key={`${sectionCode}-${group.unit}-${index}`}>
                        {entryIndex === 0 ? (
                          <td className="unit-cell fixed-unit-cell" rowSpan={group.entries.length}>
                            <div className="fixed-unit-content">
                              <strong>{group.unit}</strong>
                              <span>对象 {group.entries.length}</span>
                              <button type="button" className="unit-add-button" onClick={() => addRow(group.unit)}>
                                新增对象
                              </button>
                            </div>
                          </td>
                        ) : null}
                        <td className="object-cell">
                          <textarea
                            value={row.object_name}
                            onChange={(event) => updateRow(index, { object_name: event.target.value })}
                            rows={2}
                          />
                        </td>
                        <td className="record-cell">
                          <div className="record-input-group">
                            <textarea
                              className="record-textarea"
                              value={row.record_text}
                              onChange={(event) => {
                                rememberRecordSelection(index, event.target);
                                updateRow(index, { record_text: event.target.value });
                              }}
                              onClick={(event) => rememberRecordSelection(index, event.currentTarget)}
                              onKeyUp={(event) => rememberRecordSelection(index, event.currentTarget)}
                              onSelect={(event) => rememberRecordSelection(index, event.currentTarget)}
                              rows={5}
                            />
                            <div className="record-control-row">
                              <select
                                className="record-template-select"
                                value=""
                                disabled={templateOptionsCount === 0}
                                title={templateOptionsCount === 0 ? "当前测评单元暂无可套用模板" : "选择三类结果记录模板"}
                                onChange={(event) => applyRecordTemplate(index, event.target.value)}
                              >
                                <option value="">
                                  {templateOptionsCount > 0 ? "套用模板" : "本单元暂无三类模板"}
                                </option>
                                {templateSlots.map((slot) => (
                                  <option key={slot.id} value={slot.id}>
                                    {templateSlotOptionLabel(slot)}
                                  </option>
                                ))}
                              </select>
                              <select
                                className="reference-select"
                                value=""
                                onChange={(event) => insertReferenceToken(index, event.target.value)}
                              >
                                <option value="">插入图片引用</option>
                                {evidenceImages.map((image) => (
                                  <option key={image.id} value={image.id}>
                                    {image.figure_label ?? `${sectionCode}-${image.sort_order}`} {image.caption || image.original_name}
                                  </option>
                                ))}
                              </select>
                            </div>
                          </div>
                        </td>
                        {technical ? (
                          <>
                            {(["d", "a", "k"] as const).map((key) => (
                              <td className="metric-cell" key={key}>
                                <select
                                  className="metric-select"
                                  value={row.metric_result?.[key] ?? ""}
                                  onChange={(event) => updateMetric(index, key, event.target.value)}
                                >
                                  <option value="">选择</option>
                                  {metricOptions.map((option) => (
                                    <option key={option} value={option}>
                                      {option}
                                    </option>
                                  ))}
                                </select>
                              </td>
                            ))}
                            <td className="score-cell">
                              <input
                                className="score-input"
                                value={row.metric_result?.object_score ?? ""}
                                onChange={(event) => updateMetric(index, "object_score", event.target.value)}
                                onBlur={() => formatObjectScore(index)}
                              />
                            </td>
                            {entryIndex === 0 ? (
                              <td className="score-cell unit-score-cell" rowSpan={group.entries.length}>
                                <output className="unit-score-output">{group.unitScore}</output>
                              </td>
                            ) : null}
                          </>
                        ) : (
                          <>
                            <td className="compliance-cell">
                              <select
                                className="compliance-select"
                                value={row.metric_result?.compliance ?? ""}
                                onChange={(event) => updateMetric(index, "compliance", event.target.value)}
                              >
                                <option value="">选择</option>
                                {complianceOptions.map((option) => (
                                  <option key={option} value={option}>
                                    {option}
                                  </option>
                                ))}
                              </select>
                            </td>
                            {entryIndex === 0 ? (
                              <td className="score-cell unit-score-cell" rowSpan={group.entries.length}>
                                <input
                                  className="score-input"
                                  value={group.unitScore}
                                  onChange={(event) => updateUnitScoreForUnit(group.unit, event.target.value)}
                                />
                              </td>
                            ) : null}
                          </>
                        )}
                        <td className="row-action-cell">
                          <button type="button" className="danger-button" onClick={() => removeRow(index)}>
                            删除
                          </button>
                        </td>
                      </tr>
                    );
                  })
                )
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function insertAtSelectionOrPlaceholder(text: string, token: string, selection?: TextSelection) {
  if (!text) {
    return token;
  }

  const range = insertionRange(text, selection);
  if (!range) {
    return `${text}${token}`;
  }

  return `${text.slice(0, range.start)}${token}${text.slice(range.end)}`;
}

function insertionRange(text: string, selection?: TextSelection): TextSelection | undefined {
  if (!selection) {
    const placeholderIndex = text.indexOf(FIGURE_PLACEHOLDER);
    if (placeholderIndex >= 0) {
      return {
        start: placeholderIndex,
        end: placeholderIndex + FIGURE_PLACEHOLDER.length
      };
    }
    return undefined;
  }

  const start = Math.max(0, Math.min(selection.start, text.length));
  const end = Math.max(start, Math.min(selection.end, text.length));
  const placeholderRange = placeholderAroundSelection(text, { start, end });

  return placeholderRange ?? { start, end };
}

function placeholderAroundSelection(text: string, selection: TextSelection): TextSelection | undefined {
  let index = text.indexOf(FIGURE_PLACEHOLDER);
  while (index >= 0) {
    const end = index + FIGURE_PLACEHOLDER.length;
    const caretInsidePlaceholder = selection.start === selection.end && selection.start >= index && selection.start <= end;
    const selectionTouchesPlaceholder = selection.start < end && selection.end > index;
    if (caretInsidePlaceholder || selectionTouchesPlaceholder) {
      return { start: index, end };
    }
    index = text.indexOf(FIGURE_PLACEHOLDER, end);
  }
  return undefined;
}
