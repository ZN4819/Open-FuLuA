import type { AssessmentRowInput, TemplateProfile } from "../api/client";

type AssessmentTableProps = {
  sectionCode: string;
  rows: AssessmentRowInput[];
  profile: TemplateProfile;
  isSaving: boolean;
  isDirty: boolean;
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
  onRowsChange,
  onSave
}: AssessmentTableProps) {
  const technical = isTechnicalSection(sectionCode);
  const metricOptions = profile.content_controls.technical_metric.options;
  const complianceOptions = profile.content_controls.management_compliance.options;

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

  function insertReferenceToken(index: number) {
    const token = `[[FIG:pending-${Date.now()}]]`;
    const displayText = `${profile.sections.find((section) => section.code === sectionCode)?.figure_prefix ?? "图A-"}待关联`;
    const row = rows[index];
    const recordText = row.record_text ? `${row.record_text}${token}` : token;
    updateRow(index, {
      record_text: recordText,
      cross_references: [
        ...(row.cross_references ?? []),
        {
          target_image_id: null,
          token,
          display_text: displayText
        }
      ]
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
          <table className="assessment-table">
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
                      onChange={(event) => updateRow(index, { record_text: event.target.value })}
                      rows={6}
                    />
                    <button type="button" className="inline-action" onClick={() => insertReferenceToken(index)}>
                      插入引用
                    </button>
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
