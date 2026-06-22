import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";

import type {
  AssessmentRowInput,
  RecordTemplate,
  RecordTemplateExport,
  RecordTemplateImportPayload,
  RecordTemplateImportResult,
  RecordTemplateInput,
  TemplateProfile
} from "../api/client";

type TemplateFormState = {
  section_code: string;
  table_type: "technical" | "management";
  unit: string;
  object_name: string;
  title: string;
  record_text: string;
  tags: string;
};

type TemplateManagerPanelProps = {
  profile: TemplateProfile;
  activeSectionCode: string;
  templates: RecordTemplate[];
  currentRows: AssessmentRowInput[];
  onClose: () => void;
  onCreate: (payload: RecordTemplateInput) => Promise<RecordTemplate>;
  onUpdate: (templateId: string, payload: Partial<RecordTemplateInput>) => Promise<RecordTemplate>;
  onDelete: (templateId: string) => Promise<RecordTemplate>;
  onCopy: (templateId: string) => Promise<RecordTemplate>;
  onExportUserTemplates: () => Promise<RecordTemplateExport>;
  onPreviewImport: (payload: RecordTemplateImportPayload) => Promise<RecordTemplateImportResult>;
  onImportTemplates: (payload: RecordTemplateImportPayload) => Promise<RecordTemplateImportResult>;
  onSaveRowAsTemplate: (row: AssessmentRowInput) => Promise<RecordTemplate | undefined>;
};

function tableTypeForSection(profile: TemplateProfile, sectionCode: string): "technical" | "management" {
  return profile.sections.find((section) => section.code === sectionCode)?.table_type ?? "technical";
}

function emptyForm(profile: TemplateProfile, sectionCode: string, unit = ""): TemplateFormState {
  return {
    section_code: sectionCode,
    table_type: tableTypeForSection(profile, sectionCode),
    unit,
    object_name: "",
    title: "",
    record_text: "",
    tags: ""
  };
}

function tagsToText(tags?: string[]) {
  return tags?.join("，") ?? "";
}

function tagsFromText(value: string) {
  return value
    .split(/[，,]/)
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function templateSourceLabel(template: RecordTemplate) {
  return template.source_type === "user" ? "我的模板" : "系统模板";
}

function templateSourceClass(template: RecordTemplate) {
  return template.source_type === "user" ? "user" : "system";
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

function templateMatchesKeyword(template: RecordTemplate, keyword: string) {
  if (!keyword) {
    return true;
  }
  const content = [
    template.section_code,
    template.unit,
    template.object_name,
    template.title,
    template.record_text,
    ...(template.tags ?? [])
  ]
    .join(" ")
    .toLowerCase();
  return content.includes(keyword.toLowerCase());
}

function payloadFromForm(form: TemplateFormState): RecordTemplateInput {
  return {
    section_code: form.section_code,
    table_type: form.table_type,
    unit: form.unit.trim(),
    object_name: form.object_name.trim(),
    title: form.title.trim(),
    record_text: form.record_text.trim(),
    tags: tagsFromText(form.tags)
  };
}

function rowLabel(row: AssessmentRowInput, index: number) {
  const objectName = row.object_name.trim();
  return objectName ? `${row.unit} / ${objectName}` : `${row.unit} / 对象 ${index + 1}`;
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

function importActionLabel(action: RecordTemplateImportResult["items"][number]["action"]) {
  if (action === "create") {
    return "新增";
  }
  if (action === "update") {
    return "更新";
  }
  if (action === "error") {
    return "错误";
  }
  return "跳过";
}

export function TemplateManagerPanel({
  profile,
  activeSectionCode,
  templates,
  currentRows,
  onClose,
  onCreate,
  onUpdate,
  onDelete,
  onCopy,
  onExportUserTemplates,
  onPreviewImport,
  onImportTemplates,
  onSaveRowAsTemplate
}: TemplateManagerPanelProps) {
  const [sectionFilter, setSectionFilter] = useState(activeSectionCode);
  const [unitFilter, setUnitFilter] = useState("");
  const [keyword, setKeyword] = useState("");
  const [form, setForm] = useState(() => emptyForm(profile, activeSectionCode));
  const [editingTemplateId, setEditingTemplateId] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [busyTemplateId, setBusyTemplateId] = useState<string | null>(null);
  const [message, setMessage] = useState<string>();
  const [error, setError] = useState<string>();
  const [importPayload, setImportPayload] = useState<RecordTemplateImportPayload>();
  const [importFileName, setImportFileName] = useState<string>();
  const [importPreview, setImportPreview] = useState<RecordTemplateImportResult>();
  const [isExportingBackup, setIsExportingBackup] = useState(false);
  const [isPreviewingImport, setIsPreviewingImport] = useState(false);
  const [isImporting, setIsImporting] = useState(false);

  useEffect(() => {
    setSectionFilter(activeSectionCode);
    setUnitFilter("");
    setForm(emptyForm(profile, activeSectionCode));
    setEditingTemplateId(null);
    setMessage(undefined);
    setError(undefined);
  }, [activeSectionCode, profile]);

  const sections = profile.sections;
  const sectionTemplates = useMemo(
    () => templates.filter((template) => template.section_code === sectionFilter),
    [sectionFilter, templates]
  );
  const unitOptions = useMemo(
    () =>
      Array.from(new Set([
        ...sectionTemplates.map((template) => template.unit.trim()).filter(Boolean),
        ...currentRows
          .filter((row) => row.unit.trim() && sectionFilter === activeSectionCode)
          .map((row) => row.unit.trim())
      ])).sort((first, second) => first.localeCompare(second, "zh-CN")),
    [activeSectionCode, currentRows, sectionFilter, sectionTemplates]
  );
  const filteredTemplates = useMemo(
    () =>
      sectionTemplates
        .filter((template) => !unitFilter || template.unit === unitFilter)
        .filter((template) => templateMatchesKeyword(template, keyword))
        .sort((first, second) => {
          const sourceOrder = (first.source_type === "user" ? 0 : 1) - (second.source_type === "user" ? 0 : 1);
          return sourceOrder || first.unit.localeCompare(second.unit, "zh-CN") || first.title.localeCompare(second.title, "zh-CN");
        }),
    [keyword, sectionTemplates, unitFilter]
  );
  const saveableRows = currentRows.filter((row) => row.unit.trim() && row.record_text.trim());
  const activeEditingSection = sections.find((section) => section.code === activeSectionCode);
  const userTemplateCount = templates.filter((template) => template.source_type === "user").length;
  const systemTemplateCount = templates.length - userTemplateCount;
  const canSubmit = Boolean(form.section_code && form.record_text.trim());

  function updateForm(patch: Partial<TemplateFormState>) {
    setForm((current) => ({ ...current, ...patch }));
    setMessage(undefined);
    setError(undefined);
  }

  function handleSectionFilterChange(sectionCode: string) {
    setSectionFilter(sectionCode);
    setUnitFilter("");
    setEditingTemplateId(null);
    setForm(emptyForm(profile, sectionCode));
    setMessage(undefined);
    setError(undefined);
  }

  function handleFormSectionChange(sectionCode: string) {
    updateForm({
      section_code: sectionCode,
      table_type: tableTypeForSection(profile, sectionCode)
    });
  }

  function startCreate() {
    setEditingTemplateId(null);
    setForm(emptyForm(profile, sectionFilter, unitFilter));
    setMessage(undefined);
    setError(undefined);
  }

  function startEdit(template: RecordTemplate) {
    if (template.source_type !== "user") {
      setError("系统模板为只读模板，请先复制为我的模板后再编辑。");
      return;
    }
    setEditingTemplateId(template.id);
    setSectionFilter(template.section_code);
    setUnitFilter(template.unit);
    setForm({
      section_code: template.section_code,
      table_type: template.table_type,
      unit: template.unit,
      object_name: template.object_name,
      title: template.title,
      record_text: template.record_text,
      tags: tagsToText(template.tags)
    });
    setMessage(undefined);
    setError(undefined);
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) {
      setError("模板正文不能为空。");
      return;
    }

    setIsSubmitting(true);
    setError(undefined);
    setMessage(undefined);
    try {
      const payload = payloadFromForm(form);
      const saved = editingTemplateId
        ? await onUpdate(editingTemplateId, payload)
        : await onCreate(payload);
      setMessage(editingTemplateId ? "模板已更新。" : "模板已新增。");
      setSectionFilter(saved.section_code);
      setUnitFilter(saved.unit);
      setEditingTemplateId(null);
      setForm(emptyForm(profile, saved.section_code, saved.unit));
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存模板失败。");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleCopy(template: RecordTemplate) {
    setBusyTemplateId(template.id);
    setError(undefined);
    setMessage(undefined);
    try {
      const copied = await onCopy(template.id);
      setSectionFilter(copied.section_code);
      setUnitFilter(copied.unit);
      startEdit(copied);
      setMessage("已复制为我的模板，可继续编辑。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "复制模板失败。");
    } finally {
      setBusyTemplateId(null);
    }
  }

  async function handleDelete(template: RecordTemplate) {
    const confirmed = window.confirm(`确定删除“${template.title || template.unit}”吗？删除后不会再出现在模板下拉中。`);
    if (!confirmed) {
      return;
    }

    setBusyTemplateId(template.id);
    setError(undefined);
    setMessage(undefined);
    try {
      await onDelete(template.id);
      if (editingTemplateId === template.id) {
        startCreate();
      }
      setMessage("模板已删除。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除模板失败。");
    } finally {
      setBusyTemplateId(null);
    }
  }


  async function handleExportTemplates() {
    setIsExportingBackup(true);
    setError(undefined);
    setMessage(undefined);
    try {
      const exported = await onExportUserTemplates();
      downloadJsonFile(exported, `fulua-user-record-templates-${new Date().toISOString().slice(0, 10)}.json`);
      setMessage(`已导出 ${exported.templates.length} 条我的模板。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出模板失败。");
    } finally {
      setIsExportingBackup(false);
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
      const parsed = JSON.parse(await file.text()) as RecordTemplateImportPayload;
      if (!Array.isArray(parsed.templates)) {
        throw new Error("导入文件必须包含 templates 列表。");
      }
      setImportPayload(parsed);
      setImportFileName(file.name);
      setMessage(`已读取 ${file.name}，请先预览导入结果。`);
    } catch (err) {
      setImportPayload(undefined);
      setImportFileName(undefined);
      setError(err instanceof Error ? err.message : "读取导入文件失败。");
    }
  }

  async function handlePreviewImport() {
    if (!importPayload) {
      setError("请先选择模板备份 JSON 文件。");
      return;
    }

    setIsPreviewingImport(true);
    setError(undefined);
    setMessage(undefined);
    try {
      const result = await onPreviewImport(importPayload);
      setImportPreview(result);
      setMessage("导入预览已生成。");
    } catch (err) {
      setError(err instanceof Error ? err.message : "生成导入预览失败。");
    } finally {
      setIsPreviewingImport(false);
    }
  }

  async function handleImportTemplates() {
    if (!importPayload) {
      setError("请先选择模板备份 JSON 文件。");
      return;
    }
    if (importPreview?.summary.errors) {
      setError("导入预览仍有错误，请修正文件后再导入。");
      return;
    }

    setIsImporting(true);
    setError(undefined);
    setMessage(undefined);
    try {
      const result = await onImportTemplates(importPayload);
      setImportPreview(result);
      setMessage(`导入完成：新增 ${result.summary.created}，更新 ${result.summary.updated}，跳过 ${result.summary.skipped}。`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "导入模板失败。");
    } finally {
      setIsImporting(false);
    }
  }
  async function handleSaveRow(row: AssessmentRowInput, index: number) {
    setBusyTemplateId(`row-${index}`);
    setError(undefined);
    setMessage(undefined);
    try {
      const saved = await onSaveRowAsTemplate(row);
      if (saved) {
        setSectionFilter(saved.section_code);
        setUnitFilter(saved.unit);
        setMessage("当前测评行已保存为我的模板。");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存测评行为模板失败。");
    } finally {
      setBusyTemplateId(null);
    }
  }

  return (
    <section className="feedback-panel template-manager-panel" aria-label="结果记录模板管理">
      <div className="feedback-heading template-manager-heading">
        <div>
          <p className="eyebrow">模板知识库</p>
          <h3>结果记录模板管理</h3>
        </div>
        <div className="template-manager-actions">
          <span className="status-chip">系统 {systemTemplateCount}</span>
          <span className="status-chip">我的 {userTemplateCount}</span>
          <button type="button" className="secondary-button" onClick={startCreate}>
            新建模板
          </button>
          <button type="button" className="secondary-button" onClick={onClose}>
            收起
          </button>
        </div>
      </div>

      <div className="template-manager-tools">
        <label>
          <span>章节</span>
          <select value={sectionFilter} onChange={(event) => handleSectionFilterChange(event.target.value)}>
            {sections.map((section) => (
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
            placeholder="搜索标题、对象、正文或标签"
          />
        </label>
      </div>

      {error ? <p className="error">{error}</p> : null}
      {message ? <p className="success">{message}</p> : null}

      <div className="template-manager-grid">
        <form className="template-manager-form" onSubmit={handleSubmit}>
          <div>
            <p className="eyebrow">{editingTemplateId ? "编辑我的模板" : "新增我的模板"}</p>
            <h4>{editingTemplateId ? "修改模板内容" : "录入新模板"}</h4>
          </div>
          <div className="template-form-grid">
            <label>
              <span>章节</span>
              <select value={form.section_code} onChange={(event) => handleFormSectionChange(event.target.value)}>
                {sections.map((section) => (
                  <option key={section.code} value={section.code}>
                    {section.code} {section.title}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>测评单元</span>
              <input value={form.unit} onChange={(event) => updateForm({ unit: event.target.value })} />
            </label>
            <label>
              <span>测评对象</span>
              <input value={form.object_name} onChange={(event) => updateForm({ object_name: event.target.value })} />
            </label>
            <label>
              <span>标题</span>
              <input
                value={form.title}
                onChange={(event) => updateForm({ title: event.target.value })}
                placeholder="为空时由单元和对象生成"
              />
            </label>
            <label className="template-tags-field">
              <span>标签</span>
              <input
                value={form.tags}
                onChange={(event) => updateForm({ tags: event.target.value })}
                placeholder="多个标签用逗号分隔"
              />
            </label>
          </div>
          <label className="template-record-field">
            <span>结果记录正文</span>
            <textarea
              value={form.record_text}
              onChange={(event) => updateForm({ record_text: event.target.value })}
              rows={8}
              required
            />
          </label>
          <div className="template-form-actions">
            <button type="submit" disabled={isSubmitting || !canSubmit}>
              {isSubmitting ? "保存中..." : editingTemplateId ? "保存修改" : "新增模板"}
            </button>
            {editingTemplateId ? (
              <button type="button" className="secondary-button" onClick={startCreate} disabled={isSubmitting}>
                取消编辑
              </button>
            ) : null}
          </div>
        </form>

        <div className="template-manager-side">          <div className="template-side-block template-backup-block">
            <div className="template-side-heading">
              <div>
                <p className="eyebrow">备份与恢复</p>
                <h4>用户模板导入导出</h4>
              </div>
              <span className="status-chip">JSON</span>
            </div>
            <div className="template-backup-actions">
              <button type="button" className="secondary-button" onClick={handleExportTemplates} disabled={isExportingBackup}>
                {isExportingBackup ? "导出中..." : "导出我的模板"}
              </button>
              <label className="secondary-button template-file-button">
                选择备份 JSON
                <input type="file" accept="application/json,.json" onChange={handleImportFileChange} />
              </label>
              <button type="button" className="secondary-button" onClick={handlePreviewImport} disabled={!importPayload || isPreviewingImport}>
                {isPreviewingImport ? "预览中..." : "预览导入"}
              </button>
              <button type="button" onClick={handleImportTemplates} disabled={!importPayload || isImporting || Boolean(importPreview?.summary.errors)}>
                {isImporting ? "导入中..." : "确认导入"}
              </button>
            </div>
            {importFileName ? <p className="template-import-file">当前文件：{importFileName}</p> : null}
            {importPreview ? (
              <div className="template-import-preview">
                <div className="template-import-summary">
                  <span className="status-chip">新增 {importPreview.summary.created}</span>
                  <span className="status-chip">更新 {importPreview.summary.updated}</span>
                  <span className="status-chip">跳过 {importPreview.summary.skipped}</span>
                  <span className={importPreview.summary.errors > 0 ? "dirty-chip" : "clean-chip"}>错误 {importPreview.summary.errors}</span>
                </div>
                <div className="template-import-list">
                  {importPreview.items.map((item) => (
                    <div className={`template-import-item ${item.action}`} key={`${item.index}-${item.action}-${item.title}`}>
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
          <div className="template-side-block">
            <div className="template-side-heading">
              <div>
                <p className="eyebrow">当前编辑章节</p>
                <h4>{activeEditingSection ? `${activeEditingSection.code} ${activeEditingSection.title}` : activeSectionCode}</h4>
              </div>
              <span className="status-chip">可保存 {saveableRows.length}</span>
            </div>
            {saveableRows.length === 0 ? (
              <p className="template-empty">当前章节还没有可保存为模板的结果记录。</p>
            ) : (
              <div className="template-row-actions">
                {saveableRows.map((row, index) => (
                  <button
                    type="button"
                    className="secondary-button"
                    key={`${row.unit}-${row.object_name}-${index}`}
                    onClick={() => handleSaveRow(row, index)}
                    disabled={busyTemplateId === `row-${index}`}
                  >
                    {busyTemplateId === `row-${index}` ? "保存中..." : rowLabel(row, index)}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="template-list-heading">
            <div>
              <p className="eyebrow">模板列表</p>
              <h4>{filteredTemplates.length} 条模板</h4>
            </div>
            <span className="status-chip">{unitFilter || "全部单元"}</span>
          </div>
          {filteredTemplates.length === 0 ? (
            <p className="template-empty">没有匹配的模板，可调整筛选条件或新建模板。</p>
          ) : (
            <div className="template-list">
              {filteredTemplates.map((template) => (
                <article className="template-list-item" key={template.id}>
                  <div className="template-item-heading">
                    <div>
                      <strong>{template.title || template.object_name || template.unit}</strong>
                      <span>
                        {template.section_code} / {template.unit || "未填写单元"} / {template.object_name || "未填写对象"}
                      </span>
                    </div>
                    <span className={`template-source ${templateSourceClass(template)}`}>{templateSourceLabel(template)}</span>
                  </div>
                  <p>{template.record_text}</p>
                  <div className="template-item-meta">
                    <span>更新 {formatTemplateDate(template.updated_at)}</span>
                    {template.tags?.length ? <span>标签 {template.tags.join("，")}</span> : null}
                  </div>
                  <div className="template-list-actions">
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => handleCopy(template)}
                      disabled={busyTemplateId === template.id}
                    >
                      {busyTemplateId === template.id ? "复制中..." : "复制"}
                    </button>
                    {template.source_type === "user" ? (
                      <>
                        <button type="button" className="secondary-button" onClick={() => startEdit(template)}>
                          编辑
                        </button>
                        <button
                          type="button"
                          className="danger-button"
                          onClick={() => handleDelete(template)}
                          disabled={busyTemplateId === template.id}
                        >
                          删除
                        </button>
                      </>
                    ) : (
                      <span className="readonly-note">系统模板只读</span>
                    )}
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
