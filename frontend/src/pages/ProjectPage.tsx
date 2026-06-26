import { ChangeEvent, FormEvent, useEffect, useMemo, useState } from "react";
import {
  createProjectFromDocxImport,
  createRenderJob,
  createProject,
  deleteProject,
  exportProjectDocx,
  exportRecordTemplateSlots,
  getProject,
  getRenderJob,
  getRecordTemplateSlots,
  getSectionDetail,
  getTemplateProfile,
  importRecordTemplateSlots,
  listProjects,
  previewRecordTemplateSlotImport,
  resolveFileUrl,
  resetRecordTemplateSlot,
  updateRecordTemplateSlot,
  updateSectionDetail,
  validateProject,
  uploadDocxImport,
  type AssessmentRowInput,
  type DocxImportJob,
  type EvidenceImage,
  type Project,
  type RenderJob,
  type RecordTemplateSlot,
  type RecordTemplateSlotImportPayload,
  type RecordTemplateSlotUpdateInput,
  type SectionDetail,
  type TemplateProfile,
  type ValidationIssue,
  type ValidationResponse
} from "../api/client";
import { AssessmentTable, type SubsystemUiState } from "../components/AssessmentTable";
import { EvidencePanel } from "../components/EvidencePanel";
import { Layout } from "../components/Layout";
import { SectionNav } from "../components/SectionNav";
import { TemplateManagerPanel } from "../components/TemplateManagerPanel";

const EMPTY_SUBSYSTEM_UI_STATE: SubsystemUiState = {
  manualSubsystemNames: [],
  activeSubsystem: ""
};

function rowsFromDetail(detail: SectionDetail): AssessmentRowInput[] {
  return detail.rows.map((row) => ({
    unit: row.unit,
    object_name: row.object_name,
    subsystem: row.subsystem ?? "",
    record_text: row.record_text,
    sort_order: row.sort_order,
    metric_result: row.metric_result,
    cross_references: detail.cross_references
      .filter((reference) => reference.source_row_id === row.id)
      .map((reference) => ({
        target_image_id: reference.target_image_id,
        token: reference.token,
        display_text: reference.display_text
      }))
  }));
}

export function ProjectPage() {
  const [projectName, setProjectName] = useState("附录A测评结果记录");
  const [project, setProject] = useState<Project | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeCode, setActiveCode] = useState<string>();
  const [profile, setProfile] = useState<TemplateProfile | null>(null);
  const [recordTemplateSlots, setRecordTemplateSlots] = useState<RecordTemplateSlot[]>([]);
  const [sectionDetails, setSectionDetails] = useState<Record<string, SectionDetail>>({});
  const [draftRows, setDraftRows] = useState<Record<string, AssessmentRowInput[]>>({});
  const [subsystemUiStateBySection, setSubsystemUiStateBySection] = useState<Record<string, SubsystemUiState>>({});
  const [dirtySections, setDirtySections] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string>();
  const [isCreating, setIsCreating] = useState(false);
  const [isLoadingProjects, setIsLoadingProjects] = useState(false);
  const [openingProjectId, setOpeningProjectId] = useState<number | null>(null);
  const [deletingProjectId, setDeletingProjectId] = useState<number | null>(null);
  const [isLoadingSection, setIsLoadingSection] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isSavingAll, setIsSavingAll] = useState(false);
  const [isExporting, setIsExporting] = useState<"editable" | "final" | null>(null);
  const [isValidating, setIsValidating] = useState(false);
  const [isCreatingPreview, setIsCreatingPreview] = useState(false);
  const [renderJob, setRenderJob] = useState<RenderJob>();
  const [validation, setValidation] = useState<ValidationResponse>();
  const [saveMessage, setSaveMessage] = useState<string>();
  const [isTemplateManagerOpen, setIsTemplateManagerOpen] = useState(false);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importJob, setImportJob] = useState<DocxImportJob>();
  const [importProjectName, setImportProjectName] = useState("");
  const [isUploadingImport, setIsUploadingImport] = useState(false);
  const [isConfirmingImport, setIsConfirmingImport] = useState(false);

  const activeSection = useMemo(
    () => project?.sections.find((section) => section.code === activeCode),
    [activeCode, project]
  );

  const activeDetail = activeCode ? sectionDetails[activeCode] : undefined;
  const activeRows = activeCode ? draftRows[activeCode] ?? [] : [];
  const activeRecordTemplateSlots = useMemo(
    () => recordTemplateSlots.filter((slot) => slot.section_code === activeCode),
    [activeCode, recordTemplateSlots]
  );
  const isDirty = activeCode ? dirtySections.has(activeCode) : false;
  const dirtyCount = dirtySections.size;
  const isSavingAny = isSaving || isSavingAll;
  const activeEvidenceCount = activeDetail?.evidence_images.length ?? 0;

  useEffect(() => {
    getTemplateProfile()
      .then(setProfile)
      .catch((err) => setError(err instanceof Error ? err.message : "读取模板 profile 失败"));
    refreshRecordTemplateSlots().catch((err) => setError(err instanceof Error ? err.message : "读取三类结果记录模板失败"));
    refreshProjects();
  }, []);

  useEffect(() => {
    if (!activeCode) {
      return;
    }

    refreshRecordTemplateSlots(activeCode).catch((err) =>
      setError(err instanceof Error ? err.message : "读取三类结果记录模板失败")
    );
  }, [activeCode]);
  useEffect(() => {
    if (!project || !activeCode || sectionDetails[activeCode]) {
      return;
    }

    setIsLoadingSection(true);
    setError(undefined);
    getSectionDetail(project.id, activeCode)
      .then((detail) => {
        setSectionDetails((current) => ({ ...current, [activeCode]: detail }));
        setDraftRows((current) => ({ ...current, [activeCode]: rowsFromDetail(detail) }));
      })
      .catch((err) => setError(err instanceof Error ? err.message : "读取章节失败"))
      .finally(() => setIsLoadingSection(false));
  }, [activeCode, project, sectionDetails]);

  useEffect(() => {
    if (!renderJob || !["queued", "running"].includes(renderJob.status)) {
      return;
    }

    const timer = window.setInterval(() => {
      getRenderJob(renderJob.id)
        .then(setRenderJob)
        .catch((err) => setError(err instanceof Error ? err.message : "刷新预览状态失败"));
    }, 2000);

    return () => window.clearInterval(timer);
  }, [renderJob]);

  async function refreshProjects() {
    setIsLoadingProjects(true);
    try {
      const savedProjects = await listProjects();
      setProjects(savedProjects);
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取已有项目失败");
    } finally {
      setIsLoadingProjects(false);
    }
  }

  async function refreshRecordTemplateSlots(sectionCode?: string) {
    const slots = await getRecordTemplateSlots(sectionCode);
    setRecordTemplateSlots((current) => {
      if (!sectionCode) {
        return slots;
      }
      return [
        ...current.filter((slot) => slot.section_code !== sectionCode),
        ...slots
      ];
    });
    return slots;
  }

  function openProject(projectToOpen: Project) {
    setProject(projectToOpen);
    setSectionDetails({});
    setDraftRows({});
    setSubsystemUiStateBySection({});
    setDirtySections(new Set());
    setValidation(undefined);
    setRenderJob(undefined);
    setSaveMessage(undefined);
    setError(undefined);
    setActiveCode(projectToOpen.sections[0]?.code);
  }

  async function handleOpenProject(projectId: number) {
    setOpeningProjectId(projectId);
    setError(undefined);
    try {
      const loaded = await getProject(projectId);
      openProject(loaded);
    } catch (err) {
      setError(err instanceof Error ? err.message : "打开项目失败");
    } finally {
      setOpeningProjectId(null);
    }
  }

  async function handleDeleteProject(projectToDelete: Project) {
    const confirmed = window.confirm(`确定删除“${projectToDelete.name}”吗？删除后无法从项目列表恢复。`);
    if (!confirmed) {
      return;
    }

    setDeletingProjectId(projectToDelete.id);
    setError(undefined);
    try {
      await deleteProject(projectToDelete.id);
      setProjects((current) => current.filter((item) => item.id !== projectToDelete.id));
      setSaveMessage(`已删除 ${projectToDelete.name}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "删除项目失败");
    } finally {
      setDeletingProjectId(null);
    }
  }

  function handleBackToProjects() {
    if (dirtySections.size > 0) {
      setError("当前还有未保存的章节，请先保存后再返回项目列表。");
      return;
    }
    setProject(null);
    setActiveCode(undefined);
    setSectionDetails({});
    setDraftRows({});
    setSubsystemUiStateBySection({});
    setValidation(undefined);
    setRenderJob(undefined);
    setSaveMessage(undefined);
    refreshProjects();
  }

  function handleImportFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    setError(undefined);
    setImportJob(undefined);
    setImportFile(null);
    if (!file) {
      return;
    }
    if (!file.name.toLowerCase().endsWith(".docx")) {
      setError("请选择 .docx 文件。");
      return;
    }
    setImportFile(file);
    setImportProjectName(projectNameFromFile(file.name));
  }

  async function handleUploadDocxImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    setSaveMessage(undefined);
    if (!importFile) {
      setError("请选择要导入的 DOCX 文件。");
      return;
    }

    setIsUploadingImport(true);
    try {
      const job = await uploadDocxImport(importFile);
      setImportJob(job);
      setImportProjectName(job.suggested_project_name || projectNameFromFile(importFile.name));
      if (job.status === "failed") {
        setError(job.error_message || "DOCX 导入解析失败。");
      } else {
        setSaveMessage("DOCX 导入预览已生成。");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "上传并解析 DOCX 失败");
    } finally {
      setIsUploadingImport(false);
    }
  }

  async function handleConfirmDocxImport() {
    if (!importJob) {
      return;
    }

    setIsConfirmingImport(true);
    setError(undefined);
    setSaveMessage(undefined);
    try {
      const confirmed = await createProjectFromDocxImport(importJob.id, importProjectName.trim() || importJob.suggested_project_name);
      setImportJob(confirmed);
      if (!confirmed.created_project_id) {
        setError("导入任务未返回新项目 ID。");
        return;
      }
      const loaded = await getProject(confirmed.created_project_id);
      setProjects((current) => [loaded, ...current.filter((item) => item.id !== loaded.id)]);
      setImportFile(null);
      openProject(loaded);
      setSaveMessage(`已导入 ${loaded.name}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "确认导入失败");
    } finally {
      setIsConfirmingImport(false);
    }
  }

  async function handleCreateProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    setIsCreating(true);
    try {
      const name = projectName.trim();
      if (!name) {
        setError("请输入项目名称。");
        return;
      }
      const created = await createProject(name);
      setProjects((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      openProject(created);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建项目失败");
    } finally {
      setIsCreating(false);
    }
  }

  function handleRowsChange(code: string, rows: AssessmentRowInput[]) {
    setDraftRows((current) => ({ ...current, [code]: rows }));
    setDirtySections((current) => new Set([...current, code]));
    setSaveMessage(undefined);
  }

  function handleSubsystemUiStateChange(code: string, updater: (current: SubsystemUiState) => SubsystemUiState) {
    setSubsystemUiStateBySection((current) => ({
      ...current,
      [code]: updater(current[code] ?? EMPTY_SUBSYSTEM_UI_STATE)
    }));
  }

  async function handleUpdateRecordTemplateSlot(slotId: number, payload: RecordTemplateSlotUpdateInput) {
    const updated = await updateRecordTemplateSlot(slotId, payload);
    await refreshRecordTemplateSlots(updated.section_code);
    setSaveMessage("三类结果记录模板已保存。");
    return updated;
  }

  async function handleResetRecordTemplateSlot(slotId: number) {
    const reset = await resetRecordTemplateSlot(slotId);
    await refreshRecordTemplateSlots(reset.section_code);
    setSaveMessage("三类结果记录模板已恢复默认。");
    return reset;
  }

  async function handleExportRecordTemplateSlots() {
    return exportRecordTemplateSlots();
  }

  async function handlePreviewRecordTemplateSlotImport(payload: RecordTemplateSlotImportPayload) {
    return previewRecordTemplateSlotImport(payload);
  }

  async function handleImportRecordTemplateSlots(payload: RecordTemplateSlotImportPayload) {
    const result = await importRecordTemplateSlots(payload);
    await refreshRecordTemplateSlots();
    setSaveMessage(`三类模板配置导入完成：更新 ${result.summary.updated}，跳过 ${result.summary.skipped}。`);
    return result;
  }
  function applySavedSectionDetail(code: string, detail: SectionDetail) {
    setSectionDetails((current) => ({ ...current, [code]: detail }));
    setDraftRows((current) => ({ ...current, [code]: rowsFromDetail(detail) }));
  }

  function markSectionsSaved(codes: string[]) {
    setDirtySections((current) => {
      const next = new Set(current);
      codes.forEach((code) => next.delete(code));
      return next;
    });
  }

  async function handleSaveSection() {
    if (!project || !activeCode) {
      return;
    }

    setIsSaving(true);
    setError(undefined);
    try {
      const detail = await updateSectionDetail(project.id, activeCode, {
        rows: draftRows[activeCode] ?? []
      });
      applySavedSectionDetail(activeCode, detail);
      markSectionsSaved([activeCode]);
      setSaveMessage(`${activeCode} 已保存`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存章节失败");
    } finally {
      setIsSaving(false);
    }
  }

  async function handleSaveAllSections() {
    if (!project || dirtySections.size === 0) {
      return;
    }

    const codesToSave = Array.from(dirtySections);
    const missingDraftCodes = codesToSave.filter((code) => !Object.prototype.hasOwnProperty.call(draftRows, code));
    if (missingDraftCodes.length > 0) {
      setError(`${missingDraftCodes.join("、")} 的草稿还没有加载，无法执行全部保存。`);
      return;
    }

    setIsSavingAll(true);
    setError(undefined);
    setSaveMessage(undefined);
    try {
      const results = await Promise.allSettled(
        codesToSave.map(async (code) => {
          const detail = await updateSectionDetail(project.id, code, {
            rows: draftRows[code] ?? []
          });
          return { code, detail };
        })
      );
      const savedResults = results
        .filter((result): result is PromiseFulfilledResult<{ code: string; detail: SectionDetail }> => result.status === "fulfilled")
        .map((result) => result.value);
      const failedCodes = results
        .map((result, index) => (result.status === "rejected" ? codesToSave[index] : undefined))
        .filter((code): code is string => Boolean(code));

      savedResults.forEach(({ code, detail }) => applySavedSectionDetail(code, detail));
      markSectionsSaved(savedResults.map((result) => result.code));

      if (failedCodes.length > 0) {
        setError(`${failedCodes.join("、")} 保存失败，请检查后重试。`);
        if (savedResults.length > 0) {
          setSaveMessage(`已保存 ${savedResults.length} 个章节，${failedCodes.length} 个章节失败。`);
        }
        return;
      }

      setSaveMessage(`已保存 ${savedResults.length} 个章节`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "全部保存失败");
    } finally {
      setIsSavingAll(false);
    }
  }

  function handleImagesChange(code: string, images: EvidenceImage[]) {
    setSectionDetails((current) => {
      const detail = current[code];
      if (!detail) {
        return current;
      }
      return {
        ...current,
        [code]: {
          ...detail,
          evidence_images: images
        }
      };
    });
  }

  async function handleExport(mode: "editable" | "final") {
    if (!project) {
      return;
    }
    if (dirtySections.size > 0) {
      setError("当前还有未保存的章节，请先保存后再导出。");
      return;
    }

    setIsExporting(mode);
    setError(undefined);
    try {
      const fileName = await exportProjectDocx(project.id, mode);
      setSaveMessage(`已生成 ${fileName}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出 DOCX 失败");
    } finally {
      setIsExporting(null);
    }
  }

  async function handleValidate() {
    if (!project) {
      return;
    }
    if (dirtySections.size > 0) {
      setError("当前还有未保存的章节，请先保存后再校验。");
      return;
    }

    setIsValidating(true);
    setError(undefined);
    try {
      const result = await validateProject(project.id);
      setValidation(result);
      setSaveMessage("校验已完成");
    } catch (err) {
      setError(err instanceof Error ? err.message : "校验项目失败");
    } finally {
      setIsValidating(false);
    }
  }

  async function handleCreatePreview() {
    if (!project) {
      return;
    }
    if (dirtySections.size > 0) {
      setError("当前还有未保存的章节，请先保存后再生成预览。");
      return;
    }

    setIsCreatingPreview(true);
    setError(undefined);
    try {
      const job = await createRenderJob(project.id, "final");
      setRenderJob(job);
      setSaveMessage("预览任务已创建");
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建预览任务失败");
    } finally {
      setIsCreatingPreview(false);
    }
  }

  return (
    <Layout
      title="附录A编写工具"
      sidebar={
        project ? (
          <SectionNav
            sections={project.sections}
            activeCode={activeCode}
            dirtyCodes={dirtySections}
            onSelect={setActiveCode}
          />
        ) : (
          <p className="empty-sidebar">创建项目后显示 A-1 至 A-8 章节。</p>
        )
      }
    >
      {!project ? (
        <section className="home-page">
          <div className="home-heading">
            <p className="eyebrow">当前项目</p>
            <h2>附录A测评结果记录</h2>
          </div>
          <div className="home-grid">
            <section className="home-create-panel">
              <div>
                <p className="eyebrow">新建项目</p>
                <h3>创建附录A项目</h3>
              </div>
              <form className="project-form" onSubmit={handleCreateProject}>
                <label htmlFor="projectName">项目名称</label>
                <input
                  id="projectName"
                  value={projectName}
                  onChange={(event) => setProjectName(event.target.value)}
                  maxLength={120}
                  required
                />
                <button type="submit" disabled={isCreating}>
                  {isCreating ? "创建中..." : "创建项目"}
                </button>
              </form>
            </section>

            <section className="home-import-panel">
              <div>
                <p className="eyebrow">导入项目</p>
                <h3>导入 DOCX 创建项目</h3>
              </div>
              <form className="import-form" onSubmit={handleUploadDocxImport}>
                <label className="import-file-field" htmlFor="docxImportFile">
                  <span>DOCX 文件</span>
                  <input
                    id="docxImportFile"
                    type="file"
                    accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    onChange={handleImportFileChange}
                  />
                </label>
                {importFile ? <p className="import-file-name">{importFile.name}</p> : null}
                <button type="submit" disabled={!importFile || isUploadingImport}>
                  {isUploadingImport ? "解析中..." : "上传并解析"}
                </button>
              </form>
              {importJob ? (
                <ImportPreviewPanel
                  job={importJob}
                  projectName={importProjectName}
                  isConfirming={isConfirmingImport}
                  onProjectNameChange={setImportProjectName}
                  onConfirm={handleConfirmDocxImport}
                />
              ) : null}
            </section>

            <section className="home-projects-panel">
              <div className="project-list-header">
                <div>
                  <p className="eyebrow">已有项目</p>
                  <h3>继续之前的项目</h3>
                </div>
                <div className="project-list-tools">
                  <span>{projectCountLabel(projects.length)}</span>
                  <button type="button" onClick={refreshProjects} disabled={isLoadingProjects}>
                    {isLoadingProjects ? "刷新中..." : "刷新"}
                  </button>
                </div>
              </div>

              {isLoadingProjects && projects.length === 0 ? (
                <p className="project-empty-state">正在读取项目...</p>
              ) : projects.length === 0 ? (
                <p className="project-empty-state">还没有可打开的项目。</p>
              ) : (
                <div className="project-list">
                  {projects.map((savedProject) => (
                    <article className="project-list-item" key={savedProject.id}>
                      <div className="project-list-main">
                        <strong>{savedProject.name}</strong>
                        <dl>
                          <div>
                            <dt>更新</dt>
                            <dd>{formatDate(savedProject.updated_at)}</dd>
                          </div>
                          <div>
                            <dt>创建</dt>
                            <dd>{formatDate(savedProject.created_at)}</dd>
                          </div>
                        </dl>
                      </div>
                      <div className="project-list-actions">
                        <button
                          type="button"
                          onClick={() => handleOpenProject(savedProject.id)}
                          disabled={openingProjectId === savedProject.id || deletingProjectId === savedProject.id}
                        >
                          {openingProjectId === savedProject.id ? "打开中..." : "打开"}
                        </button>
                        <button
                          type="button"
                          className="danger-button"
                          onClick={() => handleDeleteProject(savedProject)}
                          disabled={deletingProjectId === savedProject.id || openingProjectId === savedProject.id}
                        >
                          {deletingProjectId === savedProject.id ? "删除中..." : "删除"}
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          </div>
          {error ? <p className="error">{error}</p> : null}
          {saveMessage ? <p className="success">{saveMessage}</p> : null}
        </section>
      ) : (
        <section className="panel wide-panel">
          <div className="project-header">
            <div className="project-header-main">
              <p className="eyebrow">当前项目</p>
              <h2>{project.name}</h2>
              <div className="project-status-row">
                <span className={dirtyCount > 0 ? "dirty-chip" : "clean-chip"}>
                  {dirtyCount > 0 ? `${dirtyCount} 个章节未保存` : "全部已保存"}
                </span>
                {activeCode ? <span className="status-chip">正在编辑 {activeCode}</span> : null}
              </div>
            </div>
            <div className="workspace-actions" aria-label="项目操作">
              <div className="action-group">
                <button type="button" className="secondary-button" onClick={handleBackToProjects} disabled={isSavingAny}>
                  返回项目列表
                </button>
                <button type="button" onClick={handleSaveAllSections} disabled={isSavingAny || dirtyCount === 0}>
                  {isSavingAll ? "全部保存中..." : "全部保存"}
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => setIsTemplateManagerOpen((current) => !current)}
                >
                  {isTemplateManagerOpen ? "收起模板" : "模板管理"}
                </button>
              </div>
              <div className="action-group">
                <button type="button" className="secondary-button" onClick={handleValidate} disabled={isValidating || isSavingAny}>
                  {isValidating ? "校验中..." : "校验项目"}
                </button>
                <button type="button" className="secondary-button" onClick={handleCreatePreview} disabled={isCreatingPreview || isSavingAny}>
                  {isCreatingPreview ? "创建中..." : "生成预览"}
                </button>
              </div>
              <div className="action-group">
                <button type="button" onClick={() => handleExport("editable")} disabled={isExporting !== null || isSavingAny}>
                  {isExporting === "editable" ? "生成中..." : "导出可编辑版"}
                </button>
                <button type="button" onClick={() => handleExport("final")} disabled={isExporting !== null || isSavingAny}>
                  {isExporting === "final" ? "生成中..." : "导出最终版"}
                </button>
              </div>
            </div>
          </div>
          {activeSection ? (
            <div className="section-summary">
              <span className="section-code">{activeSection.code}</span>
              <div className="section-summary-main">
                <h3>{activeSection.title}</h3>
                <p>{activeSection.table_title}</p>
              </div>
              <div className="section-summary-meta" aria-label="当前章节状态">
                <span className={isDirty ? "dirty-chip" : "clean-chip"}>{isDirty ? "未保存" : "已保存"}</span>
                <span className="status-chip">测评对象 {activeRows.length}</span>
                <span className="status-chip">证据 {activeEvidenceCount}</span>
              </div>
            </div>
          ) : null}

          {error ? <p className="error">{error}</p> : null}
          {saveMessage ? <p className="success">{saveMessage}</p> : null}

          {isLoadingSection ? <p className="loading-text">正在读取章节...</p> : null}

          {validation ? <ValidationPanel validation={validation} /> : null}
          {renderJob ? <PreviewPanel job={renderJob} /> : null}

          {profile && activeCode && isTemplateManagerOpen ? (
            <TemplateManagerPanel
              profile={profile}
              activeSectionCode={activeCode}
              recordTemplateSlots={recordTemplateSlots}
              onClose={() => setIsTemplateManagerOpen(false)}
              onUpdateSlot={handleUpdateRecordTemplateSlot}
              onResetSlot={handleResetRecordTemplateSlot}
              onExportSlots={handleExportRecordTemplateSlots}
              onPreviewImportSlots={handlePreviewRecordTemplateSlotImport}
              onImportSlots={handleImportRecordTemplateSlots}
            />
          ) : null}

          {profile && activeCode && activeDetail ? (
            <AssessmentTable
              sectionCode={activeCode}
              rows={activeRows}
              profile={profile}
              isSaving={isSavingAny}
              isDirty={isDirty}
              evidenceImages={activeDetail.evidence_images}
              recordTemplateSlots={activeRecordTemplateSlots}
              subsystemUiState={subsystemUiStateBySection[activeCode] ?? EMPTY_SUBSYSTEM_UI_STATE}
              onRowsChange={(rows) => handleRowsChange(activeCode, rows)}
              onSubsystemUiStateChange={(updater) => handleSubsystemUiStateChange(activeCode, updater)}
              onSave={handleSaveSection}
            />
          ) : null}

          {project && activeCode && activeDetail ? (
            <EvidencePanel
              projectId={project.id}
              sectionCode={activeCode}
              images={activeDetail.evidence_images}
              onImagesChange={(images) => handleImagesChange(activeCode, images)}
              onError={setError}
            />
          ) : null}
        </section>
      )}
    </Layout>
  );
}


type ImportPreviewPanelProps = {
  job: DocxImportJob;
  projectName: string;
  isConfirming: boolean;
  onProjectNameChange: (value: string) => void;
  onConfirm: () => void;
};

function ImportPreviewPanel({ job, projectName, isConfirming, onProjectNameChange, onConfirm }: ImportPreviewPanelProps) {
  const summary = job.summary ?? {};
  const canConfirm = job.status === "preview_ready" && job.can_create_project && projectName.trim().length > 0;
  const visibleIssues = job.issues.slice(0, 8);

  return (
    <div className={`import-preview-panel ${importStatusClass(job)}`} aria-live="polite">
      <div className="import-status-row">
        <span className={`preview-status ${importStatusClass(job)}`}>{importStatusLabel(job.status)}</span>
        <span>{job.can_create_project ? "可创建项目" : "需处理后创建"}</span>
      </div>

      <div className="import-summary-grid" aria-label="导入解析摘要">
        <ImportSummaryMetric label="章节" value={summary.sections ?? job.sections.length} />
        <ImportSummaryMetric label="测评行" value={summary.assessment_rows ?? 0} />
        <ImportSummaryMetric label="图片" value={summary.images ?? 0} />
        <ImportSummaryMetric label="引用" value={summary.references ?? 0} />
        <ImportSummaryMetric label="错误" value={summary.errors ?? 0} />
        <ImportSummaryMetric label="警告" value={summary.warnings ?? 0} />
      </div>

      {job.sections.length > 0 ? (
        <div className="import-section-list" aria-label="导入章节预览">
          {job.sections.map((section) => (
            <div className="import-section-item" key={section.code}>
              <strong>{section.code}</strong>
              <span>{section.title}</span>
              <small>{section.row_count} 行 / {section.image_count} 图 / {section.reference_count} 引用</small>
            </div>
          ))}
        </div>
      ) : null}

      {visibleIssues.length > 0 ? (
        <ul className="import-issue-list" aria-label="导入问题清单">
          {visibleIssues.map((issue, index) => (
            <li className={issue.severity} key={`${issue.code}-${issue.section_code ?? "all"}-${index}`}>
              <span>{importIssueSeverityLabel(issue.severity)}</span>
              <div>
                <strong>{issue.message}</strong>
                <small>{[issue.section_code, issue.code, issue.target].filter(Boolean).join(" / ")}</small>
              </div>
            </li>
          ))}
          {job.issues.length > visibleIssues.length ? <li className="info">还有 {job.issues.length - visibleIssues.length} 项未显示</li> : null}
        </ul>
      ) : null}

      {job.error_message ? <p className="import-error-text">{job.error_message}</p> : null}

      {job.status === "preview_ready" ? (
        <div className="import-project-field">
          <label htmlFor="importProjectName">新项目名称</label>
          <input
            id="importProjectName"
            value={projectName}
            onChange={(event) => onProjectNameChange(event.target.value)}
            maxLength={120}
          />
          <button className="import-confirm-button" type="button" onClick={onConfirm} disabled={!canConfirm || isConfirming}>
            {isConfirming ? "创建中..." : "确认创建项目"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function ImportSummaryMetric({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function projectNameFromFile(fileName: string) {
  return fileName.replace(/\.docx$/i, "").trim() || "导入项目";
}

function importStatusLabel(status: string) {
  if (status === "preview_ready") {
    return "预览完成";
  }
  if (status === "succeeded") {
    return "已创建项目";
  }
  if (status === "failed") {
    return "解析失败";
  }
  if (status === "importing") {
    return "创建中";
  }
  if (status === "parsing") {
    return "解析中";
  }
  return status || "已上传";
}

function importStatusClass(job: DocxImportJob) {
  if (job.status === "failed" || !job.can_create_project) {
    return "error";
  }
  if (job.status === "preview_ready" || job.status === "succeeded") {
    return "success";
  }
  return "pending";
}

function importIssueSeverityLabel(severity: string) {
  if (severity === "error") {
    return "错误";
  }
  if (severity === "warning") {
    return "警告";
  }
  return "提示";
}
function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("zh-CN", { hour12: false });
}

function projectCountLabel(count: number) {
  return count > 0 ? `${count} 个项目` : "无项目";
}

const severityOrder: Record<ValidationIssue["severity"], number> = {
  error: 0,
  warning: 1,
  info: 2
};

function ValidationPanel({ validation }: { validation: ValidationResponse }) {
  const { summary, issues } = validation;
  const issueCount = issues.length;
  const sortedIssues = [...issues].sort((first, second) => severityOrder[first.severity] - severityOrder[second.severity]);

  return (
    <section className={`feedback-panel validation-panel${summary.errors > 0 ? " has-errors" : ""}`} aria-label="校验结果">
      <div className="feedback-heading">
        <div>
          <p className="eyebrow">校验反馈</p>
          <h3>项目校验结果</h3>
        </div>
        <span className={summary.errors > 0 ? "dirty-chip" : "clean-chip"}>
          {issueCount > 0 ? `发现 ${issueCount} 项` : "未发现问题"}
        </span>
      </div>

      <div className="validation-summary">
        <ValidationMetric label="错误" value={summary.errors} severity="error" note="导出前需处理" />
        <ValidationMetric label="警告" value={summary.warnings} severity="warning" note="建议确认" />
        <ValidationMetric label="提示" value={summary.info} severity="info" note="可按需优化" />
      </div>

      {issues.length === 0 ? (
        <p className="validation-empty">未发现需要处理的问题。</p>
      ) : (
        <ul className="validation-list">
          {sortedIssues.map((issue) => (
            <ValidationIssueItem issue={issue} key={issue.id ?? `${issue.code}-${issue.target_type}-${issue.target_id}`} />
          ))}
        </ul>
      )}
    </section>
  );
}

function ValidationMetric({
  label,
  value,
  severity,
  note
}: {
  label: string;
  value: number;
  severity: ValidationIssue["severity"];
  note: string;
}) {
  return (
    <div className={`validation-metric ${severity}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{note}</small>
    </div>
  );
}

function ValidationIssueItem({ issue }: { issue: ValidationIssue }) {
  return (
    <li className={`validation-item ${issue.severity}`}>
      <span className={`severity ${issue.severity}`}>{severityLabel(issue.severity)}</span>
      <div>
        <strong>{issue.message}</strong>
        <p>
          <span>{issue.code}</span>
          {issue.target_type ? <span>{issue.target_type}</span> : null}
          {issue.target_id ? <span>{issue.target_id}</span> : null}
        </p>
      </div>
    </li>
  );
}

function severityLabel(severity: ValidationIssue["severity"]) {
  if (severity === "error") {
    return "错误";
  }
  if (severity === "warning") {
    return "警告";
  }
  return "提示";
}

function PreviewPanel({ job }: { job: RenderJob }) {
  const isWorking = job.status === "queued" || job.status === "running";
  const isFailed = job.status === "failed" || job.status === "timeout";
  const hasOutputLinks = Boolean(job.output_pdf_url || job.output_docx_url);

  return (
    <section className={`feedback-panel preview-panel ${job.status}`} aria-label="预览任务状态">
      <div className="feedback-heading">
        <div>
          <p className="eyebrow">预览反馈</p>
          <h3>预览任务 #{job.id}</h3>
        </div>
        <span className={`preview-status ${renderStatusTone(job.status)}`}>{renderStatusLabel(job.status)}</span>
      </div>

      <div className="preview-detail-grid">
        <div>
          <span>状态</span>
          <strong>{renderStatusLabel(job.status)}</strong>
          <small>{renderStatusHint(job.status)}</small>
        </div>
        <div>
          <span>页数</span>
          <strong>{job.page_count ?? "-"}</strong>
          <small>PDF 预览页数</small>
        </div>
        <div>
          <span>类型</span>
          <strong>{renderModeLabel(job.mode)}</strong>
          <small>预览生成模式</small>
        </div>
      </div>

      {isWorking ? <p className="preview-pending">预览任务正在处理，页面会自动刷新状态。</p> : null}

      {job.status === "succeeded" ? (
        hasOutputLinks ? (
          <div className="preview-link-grid">
            {job.output_pdf_url ? (
              <a className="preview-link primary" href={resolveFileUrl(job.output_pdf_url)} target="_blank" rel="noreferrer">
                PDF 预览
              </a>
            ) : null}
            {job.output_docx_url ? (
              <a className="preview-link" href={resolveFileUrl(job.output_docx_url)} target="_blank" rel="noreferrer">
                预览 DOCX
              </a>
            ) : null}
            {job.log_url ? (
              <a className="preview-log" href={resolveFileUrl(job.log_url)} target="_blank" rel="noreferrer">
                查看日志
              </a>
            ) : null}
          </div>
        ) : (
          <p className="preview-warning">预览已完成，但没有返回可打开的 PDF 或 DOCX 链接。</p>
        )
      ) : null}

      {isFailed ? <p className="preview-error">{previewErrorMessage(job)}</p> : null}

      {job.status !== "succeeded" && job.log_url ? (
        <div className="preview-link-grid compact">
          <a className="preview-log" href={resolveFileUrl(job.log_url)} target="_blank" rel="noreferrer">
            查看预览日志
          </a>
        </div>
      ) : null}
    </section>
  );
}

function renderModeLabel(mode: RenderJob["mode"]) {
  return mode === "editable" ? "可编辑版" : "最终版";
}

function renderStatusLabel(status: RenderJob["status"]) {
  if (status === "queued") {
    return "排队中";
  }
  if (status === "running") {
    return "生成中";
  }
  if (status === "succeeded") {
    return "已完成";
  }
  if (status === "timeout") {
    return "已超时";
  }
  return "失败";
}

function renderStatusTone(status: RenderJob["status"]) {
  if (status === "succeeded") {
    return "success";
  }
  if (status === "failed" || status === "timeout") {
    return "danger";
  }
  return "info";
}

function renderStatusHint(status: RenderJob["status"]) {
  if (status === "queued") {
    return "等待本地渲染器处理";
  }
  if (status === "running") {
    return "正在生成 DOCX 和 PDF";
  }
  if (status === "succeeded") {
    return "可打开预览文件";
  }
  if (status === "timeout") {
    return "等待时间已超过限制";
  }
  return "需要查看失败原因";
}

function previewErrorMessage(job: RenderJob) {
  const rawMessage = job.error_message?.trim();
  if (!rawMessage) {
    return job.status === "timeout"
      ? "预览生成超过等待时间，请确认本机 Word 或 LibreOffice 可用后重试。"
      : "预览生成失败，请查看日志确认本机渲染器或文件路径是否正常。";
  }

  const lowerMessage = rawMessage.toLowerCase();
  if (job.status === "timeout" || lowerMessage.includes("timeout") || lowerMessage.includes("timed out")) {
    return `预览生成超过等待时间，请稍后重试或查看日志。原始信息：${rawMessage}`;
  }
  if (lowerMessage.includes("not found") || lowerMessage.includes("no such file") || rawMessage.includes("未找到")) {
    return `未找到可用的 Word 或 LibreOffice 渲染器，请安装或配置渲染器后重试。原始信息：${rawMessage}`;
  }
  if (lowerMessage.includes("libreoffice")) {
    return `LibreOffice 转换 PDF 未完成，请确认 LibreOffice 可用后重试。原始信息：${rawMessage}`;
  }
  if (lowerMessage.includes("word")) {
    return `Microsoft Word 自动化预览未完成，请确认 Word 可正常启动后重试。原始信息：${rawMessage}`;
  }
  return rawMessage;
}
