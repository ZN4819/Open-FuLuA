import { useRef } from "react";

import type { AssessmentRowInput, EvidenceImage, RecordTemplate, TemplateProfile } from "../api/client";

type AssessmentTableProps = {
  sectionCode: string;
  rows: AssessmentRowInput[];
  profile: TemplateProfile;
  isSaving: boolean;
  isDirty: boolean;
  evidenceImages: EvidenceImage[];
  recordTemplates: RecordTemplate[];
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

function createEmptyRow(sortOrder: number): AssessmentRowInput {
  return {
    unit: "",
    object_name: "",
    record_text: "",
    sort_order: sortOrder,
    metric_result: { ...EMPTY_METRIC },
    cross_references: []
  };
}

function normalizeRows(rows: AssessmentRowInput[]) {
  return rows.map((row, index) => ({
    ...row,
    sort_order: index + 1,
    metric_result: row.metric_result ?? { ...EMPTY_METRIC },
    cross_references: row.cross_references ?? []
  }));
}

export function AssessmentTable({
  sectionCode,
  rows,
  profile,
  isSaving,
  isDirty,
  evidenceImages,
  recordTemplates,
  onRowsChange,
  onSave
}: AssessmentTableProps) {
  const technical = isTechnicalSection(sectionCode);
  const metricOptions = profile.content_controls.technical_metric.options;
  const complianceOptions = profile.content_controls.management_compliance.options;
  const recordSelections = useRef<Record<number, TextSelection>>({});

  function updateRow(index: number, patch: Partial<AssessmentRowInput>) {
    const next = normalizeRows(
      rows.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row))
    );
    onRowsChange(next);
  }

  function updateMetric(index: number, key: string, value: string) {
    const row = rows[index];
    updateRow(index, {
      metric_result: {
        ...(row.metric_result ?? EMPTY_METRIC),
        [key]: value
      }
    });
  }

  function addRow() {
    onRowsChange([...normalizeRows(rows), createEmptyRow(rows.length + 1)]);
  }

  function removeRow(index: number) {
    onRowsChange(normalizeRows(rows.filter((_, rowIndex) => rowIndex !== index)));
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
    const displayText = image.figure_label ?? `${profile.sections.find((section) => section.code === sectionCode)?.figure_prefix ?? "图A-"}${image.sort_order}`;
    const row = rows[index];
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

  function applyRecordTemplate(index: number, templateId: string) {
    const template = recordTemplates.find((item) => item.id === templateId);
    if (!template) {
      return;
    }

    const row = rows[index];
    updateRow(index, {
      unit: row.unit || template.unit,
      object_name: row.object_name || template.object_name,
      record_text: template.record_text
    });
  }

  return (
    <div className="editor-block">
      <div className="editor-toolbar">
        <div>
          <p className="eyebrow">{technical ? "技术测评表" : "管理测评表"}</p>
          <h3>{technical ? "D / A / K 指标录入" : "符合情况录入"}</h3>
        </div>
        <div className="toolbar-actions">
          {isDirty ? <span className="dirty-chip">有未保存修改</span> : <span className="clean-chip">已保存</span>}
          <button type="button" onClick={addRow}>
            新增行
          </button>
          <button type="button" onClick={onSave} disabled={isSaving || !isDirty}>
            {isSaving ? "保存中..." : "保存"}
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="empty-table">
          <p>当前章节还没有测评行。</p>
          <button type="button" onClick={addRow}>
            添加第一行
          </button>
        </div>
      ) : (
        <div className="table-scroll">
          <table className={`assessment-table ${technical ? "technical-table" : "management-table"}`}>
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
              {rows.map((row, index) => (
                <tr key={`${sectionCode}-${index}`}>
                  <td>
                    <textarea
                      value={row.unit}
                      onChange={(event) => updateRow(index, { unit: event.target.value })}
                      rows={3}
                    />
                  </td>
                  <td>
                    <textarea
                      value={row.object_name}
                      onChange={(event) => updateRow(index, { object_name: event.target.value })}
                      rows={3}
                    />
                  </td>
                  <td className="record-cell">
                    <textarea
                      value={row.record_text}
                      onChange={(event) => {
                        rememberRecordSelection(index, event.target);
                        updateRow(index, { record_text: event.target.value });
                      }}
                      onClick={(event) => rememberRecordSelection(index, event.currentTarget)}
                      onKeyUp={(event) => rememberRecordSelection(index, event.currentTarget)}
                      onSelect={(event) => rememberRecordSelection(index, event.currentTarget)}
                      rows={6}
                    />
                    {recordTemplates.length > 0 ? (
                      <select
                        className="record-template-select"
                        value=""
                        onChange={(event) => applyRecordTemplate(index, event.target.value)}
                      >
                        <option value="">套用结果模板</option>
                        {recordTemplates.map((template) => (
                          <option key={template.id} value={template.id}>
                            {template.title}
                          </option>
                        ))}
                      </select>
                    ) : null}
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
                  </td>
                  {technical ? (
                    <>
                      {(["d", "a", "k"] as const).map((key) => (
                        <td key={key}>
                          <select
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
                      <td>
                        <input
                          value={row.metric_result?.object_score ?? ""}
                          onChange={(event) => updateMetric(index, "object_score", event.target.value)}
                        />
                      </td>
                      <td>
                        <input
                          value={row.metric_result?.unit_score ?? ""}
                          onChange={(event) => updateMetric(index, "unit_score", event.target.value)}
                        />
                      </td>
                    </>
                  ) : (
                    <>
                      <td>
                        <select
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
                      <td>
                        <input
                          value={row.metric_result?.unit_score ?? ""}
                          onChange={(event) => updateMetric(index, "unit_score", event.target.value)}
                        />
                      </td>
                    </>
                  )}
                  <td>
                    <button type="button" className="danger-button" onClick={() => removeRow(index)}>
                      删除
                    </button>
                  </td>
                </tr>
              ))}
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
