import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, changeProjectWorkflow, type Project } from "../api/client.ts";
import { ReportBasicDataWorkspace } from "./ReportBasicDataWorkspace.tsx";
import {
  confirmAppendixBindings,
  createAssessmentObject,
  createCorrectionRelation,
  createObjectRelation,
  createReportBlock,
  deleteAssessmentObject,
  deleteCorrectionRelation,
  deleteObjectRelation,
  deleteReportBlock,
  getReportMetadata,
  getReportNumberAvailability,
  getReportOverview,
  getReportSection,
  getReportSections,
  getReportSystemProfile,
  getReportTemplateRuleHints,
  listAssessmentObjects,
  listCorrectionRelations,
  listDuplicateObjectCandidates,
  listObjectRelations,
  mergeAssessmentObjects,
  previewAppendixBindings,
  updateAssessmentObject,
  updateCorrectionRelation,
  updateObjectRelation,
  updateReportBlock,
  updateReportMetadata,
  updateReportSection,
  upsertObjectSubsystem,
  validateReport,
  type AssessmentMethod,
  type AssessmentObject,
  type AssessmentObjectInput,
  type BindingPreview,
  type BindingPreviewItem,
  type CorrectionKind,
  type CorrectionRelation,
  type CorrectionRelationInput,
  type DataTableBlockPayload,
  type DuplicateObjectGroup,
  type FigureBlockPayload,
  type KeyValueBlockPayload,
  type ListBlockPayload,
  type ObjectRelation,
  type ObjectRelationInput,
  type ObjectRelationType,
  type ParagraphBlockPayload,
  type ReportBlock,
  type ReportBlockPayload,
  type ReportBlockType,
  type ReportCompletionStatus,
  type ReportIssue,
  type ReportMetadata,
  type ReportNumberAvailability,
  type ReportOverview,
  type ReportSection,
  type ReportSectionDetail,
  type ReferenceBlockPayload,
  type ReportValidation
} from "../api/reportClient.ts";
import {
  parseProjectWorkspacePath,
  projectTypeLabel,
  projectWorkspacePath,
  workflowStatusLabel,
  type ReportWorkspaceRoute
} from "../projectContracts.ts";

type ReportWorkbenchProps = {
  project: Project;
  onBack: () => void;
  onOpenAppendix: (sectionCode?: string, preserveLocation?: boolean) => void;
  onProjectUpdated: (project: Project) => void;
};

type ConflictState = {
  message: string;
  currentRevision?: number;
};

const OBJECT_TYPE_OPTIONS = [
  { value: "physical", label: "物理环境" },
  { value: "network", label: "网络通道" },
  { value: "device", label: "设备" },
  { value: "application", label: "应用" },
  { value: "data", label: "重要数据" },
  { value: "management", label: "管理对象" },
  { value: "other", label: "其他" }
] as const;

type AssessmentObjectType = typeof OBJECT_TYPE_OPTIONS[number]["value"];

const EDITABLE_BLOCK_TYPES: ReportBlockType[] = [
  "paragraph", "bullet_list", "numbered_list", "key_value_table", "data_table", "figure", "reference"
];

const SECTION_OBJECT_TYPES: Record<string, AssessmentObjectType> = {
  "A-1": "physical", "A-2": "network", "A-3": "device", "A-4": "application",
  "A-5": "management", "A-6": "management", "A-7": "management", "A-8": "management"
};

const ASSESSMENT_METHODS: AssessmentMethod[] = ["访谈", "文档审查", "现场检查", "配置检查", "工具测试"];

const OBJECT_RELATION_OPTIONS: Array<{ value: ObjectRelationType; label: string }> = [
  { value: "contains", label: "包含" }, { value: "connects", label: "连接" },
  { value: "depends_on", label: "依赖" }, { value: "protects", label: "保护" },
  { value: "uses", label: "使用" }, { value: "other", label: "其他" }
];

const CORRECTION_METRIC_PAIRS: Record<CorrectionKind, { a2: string; a4: string; label: string }> = {
  confidentiality: { a2: "通信过程中重要数据的机密性", a4: "重要数据传输机密性", label: "机密性" },
  integrity: { a2: "通信数据完整性", a4: "重要数据传输完整性", label: "完整性" }
};

export function ReportWorkbench({ project, onBack, onOpenAppendix, onProjectUpdated }: ReportWorkbenchProps) {
  const [overview, setOverview] = useState<ReportOverview>();
  const [sections, setSections] = useState<ReportSection[]>([]);
  const [validation, setValidation] = useState<ReportValidation>();
  const [pendingTemplateHintCount, setPendingTemplateHintCount] = useState<number>();
  const [route, setRoute] = useState<ReportWorkspaceRoute>(() => routeFromLocation(project.project_uuid));
  const [dirtyOwner, setDirtyOwner] = useState<string>();
  const [editorEpoch, setEditorEpoch] = useState(0);
  const [workflowStatus, setWorkflowStatus] = useState(project.workflow_status);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const [workflowError, setWorkflowError] = useState<string>();
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string>();

  const handleDirty = useCallback((owner: string, dirty: boolean) => {
    setDirtyOwner((current) => dirty ? owner : current === owner ? undefined : current);
  }, []);
  const handleMetadataDirty = useCallback((dirty: boolean) => handleDirty("metadata", dirty), [handleDirty]);
  const handleBasicsDirty = useCallback((dirty: boolean) => handleDirty("basics", dirty), [handleDirty]);
  const handleObjectsDirty = useCallback((dirty: boolean) => handleDirty("objects", dirty), [handleDirty]);
  const handleSectionDirty = useCallback((dirty: boolean) => {
    if (route.view === "section") {
      handleDirty(`section:${route.sectionKey}`, dirty);
    }
  }, [handleDirty, route]);

  const loadWorkspace = useCallback(async () => {
    setIsLoading(true);
    setError(undefined);
    try {
      const [nextOverview, nextSections, templateHints] = await Promise.all([
        getReportOverview(project.project_uuid),
        getReportSections(project.project_uuid),
        project.template_package_id
          ? getReportTemplateRuleHints(project.template_package_id)
          : Promise.resolve({ package_id: "", rules: [] })
      ]);
      setOverview(nextOverview);
      setWorkflowStatus(nextOverview.workflow_status);
      setSections([...nextSections].sort((first, second) => first.sort_order - second.sort_order));
      setPendingTemplateHintCount(templateHints.rules.filter((hint) => hint.approval_status === "pending").length);
    } catch (loadError) {
      setError(errorMessage(loadError, "读取完整报告工作台失败"));
    } finally {
      setIsLoading(false);
    }
  }, [project.project_uuid, project.template_package_id]);

  const loadValidation = useCallback(async () => {
    try {
      setValidation(await validateReport(project.project_uuid));
    } catch {
      // 校验摘要是辅助区域；主工作台仍可继续读取和编辑。
      setValidation(undefined);
    }
  }, [project.project_uuid]);

  useEffect(() => {
    void loadWorkspace();
    void loadValidation();
  }, [loadValidation, loadWorkspace]);

  useEffect(() => {
    function handleBeforeUnload(event: BeforeUnloadEvent) {
      if (!dirtyOwner) {
        return;
      }
      event.preventDefault();
      event.returnValue = "";
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [dirtyOwner]);

  useEffect(() => {
    function handlePopState() {
      const parsed = parseProjectWorkspacePath(window.location.pathname);
      if (dirtyOwner && !window.confirm("当前页面有未保存内容，确定离开并放弃这些修改吗？")) {
        window.history.pushState({}, "", projectWorkspacePath(project.project_uuid, route));
        return;
      }
      if (dirtyOwner) {
        // 浏览器历史可能落到相同路由；强制重挂载编辑器，确保“放弃”真的丢弃本地草稿。
        setEditorEpoch((current) => current + 1);
      }
      setDirtyOwner(undefined);
      if (!parsed || parsed.projectUuid !== project.project_uuid) {
        onBack();
        return;
      }
      if (parsed.route.view === "appendix_a") {
        onOpenAppendix(parsed.route.sectionCode, true);
        return;
      }
      setRoute(parsed.route);
    }
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [dirtyOwner, onBack, onOpenAppendix, project.project_uuid, route]);

  function navigate(nextRoute: ReportWorkspaceRoute) {
    if (sameRoute(route, nextRoute)) {
      return;
    }
    if (dirtyOwner && !window.confirm("当前页面有未保存内容，确定离开并放弃这些修改吗？")) {
      return;
    }
    setDirtyOwner(undefined);
    setRoute(nextRoute);
    window.history.pushState({}, "", projectWorkspacePath(project.project_uuid, nextRoute));
  }

  function handleBack() {
    if (dirtyOwner && !window.confirm("当前页面有未保存内容，确定返回并放弃这些修改吗？")) {
      return;
    }
    setDirtyOwner(undefined);
    window.history.pushState({}, "", "/");
    onBack();
  }

  function handleOpenAppendix(sectionCode?: string) {
    if (dirtyOwner && !window.confirm("当前页面有未保存内容，确定进入附录 A 并放弃这些修改吗？")) {
      return;
    }
    setDirtyOwner(undefined);
    onOpenAppendix(sectionCode, false);
  }

  async function handleWorkflowTransition() {
    if (dirtyOwner) {
      setWorkflowError("当前页面仍有未保存内容，请先保存后再变更复核状态。");
      return;
    }
    const action = workflowStatus === "ready_for_review" ? "reopen" : "ready-for-review";
    setIsTransitioning(true);
    setWorkflowError(undefined);
    try {
      const updated = await changeProjectWorkflow(project.project_uuid, action);
      setWorkflowStatus(updated.workflow_status);
      onProjectUpdated(updated);
      void loadWorkspace();
      void loadValidation();
    } catch (transitionError) {
      setWorkflowError(errorMessage(transitionError, "变更复核状态失败"));
    } finally {
      setIsTransitioning(false);
    }
  }

  const activeSection = route.view === "section"
    ? sections.find((section) => section.section_key === route.sectionKey)
    : undefined;

  return (
    <section className="report-workbench" aria-label="完整报告章节工作台">
      <header className="report-workbench-header">
        <div className="project-header-main">
          <p className="eyebrow">完整报告项目</p>
          <h2>{project.name}</h2>
          <div className="project-status-row">
            <span className="project-type-badge full_report">{projectTypeLabel(project.project_type)}</span>
            <span className="workflow-status-badge">{workflowStatusLabel(workflowStatus)}</span>
            <span className="template-version-badge">母版 {project.template_revision ?? "未绑定"}</span>
            <span className={dirtyOwner ? "dirty-chip" : "clean-chip"}>{dirtyOwner ? "有未保存内容" : "当前页面已保存"}</span>
          </div>
        </div>
        <div className="workspace-actions">
          <button type="button" className="secondary-button" onClick={handleBack}>返回项目列表</button>
          <button type="button" onClick={() => handleOpenAppendix()}>进入附录 A</button>
          {workflowStatus !== "confirmed" ? (
            <button type="button" className="secondary-button" onClick={() => void handleWorkflowTransition()} disabled={isTransitioning || Boolean(dirtyOwner)}>
              {isTransitioning ? "处理中..." : workflowStatus === "ready_for_review" ? "重新打开编辑" : "提交复核"}
            </button>
          ) : null}
        </div>
      </header>

      {workflowError ? <p className="error" role="alert">{workflowError}</p> : null}

      {error ? (
        <div className="report-workbench-failure" role="alert">
          <p>{error}</p>
          <button type="button" onClick={loadWorkspace}>重新读取</button>
        </div>
      ) : null}

      <div className="report-workbench-grid">
        <ReportSectionTree
          sections={sections}
          route={route}
          dirtyOwner={dirtyOwner}
          isLoading={isLoading}
          onNavigate={navigate}
          onOpenAppendix={handleOpenAppendix}
        />

        <main className="report-workbench-main" tabIndex={-1}>
          {isLoading ? <p className="report-loading" aria-live="polite">正在读取报告章节...</p> : null}
          {!isLoading && route.view === "overview" ? (
            <ReportOverviewPanel
              key={`overview-${editorEpoch}`}
              projectUuid={project.project_uuid}
              overview={overview}
              sections={sections}
              onDirtyChange={handleMetadataDirty}
              onNavigate={navigate}
              onSaved={() => { void loadWorkspace(); void loadValidation(); }}
            />
          ) : null}
          {!isLoading && route.view === "objects" ? (
            <ReportObjectLibrary
              key={`objects-${editorEpoch}`}
              projectUuid={project.project_uuid}
              onDirtyChange={handleObjectsDirty}
              onChanged={() => void loadWorkspace()}
            />
          ) : null}
          {!isLoading && route.view === "basics" ? (
            <ReportBasicDataWorkspace
              key={`basics-${editorEpoch}`}
              projectUuid={project.project_uuid}
              onDirtyChange={handleBasicsDirty}
              onChanged={() => { void loadWorkspace(); void loadValidation(); }}
            />
          ) : null}
          {!isLoading && route.view === "section" && activeSection ? (
            <ReportSectionWorkspace
              key={`${activeSection.section_uuid}-${editorEpoch}`}
              projectUuid={project.project_uuid}
              section={activeSection}
              onDirtyChange={handleSectionDirty}
              onOpenAppendix={handleOpenAppendix}
              onSaved={() => { void loadWorkspace(); void loadValidation(); }}
            />
          ) : null}
          {!isLoading && route.view === "section" && !activeSection ? (
            <div className="report-empty-state" role="alert">
              <h3>未找到该章节</h3>
              <p>当前深链接不属于该母版的章节清单，请返回概览重新选择。</p>
              <button type="button" onClick={() => navigate({ view: "overview" })}>返回报告概览</button>
            </div>
          ) : null}
        </main>

        <ReportContextPanel
          overview={overview}
          validation={validation}
          activeSection={activeSection}
          pendingTemplateHintCount={pendingTemplateHintCount}
        />
      </div>
    </section>
  );
}

type ReportSectionTreeProps = {
  sections: ReportSection[];
  route: ReportWorkspaceRoute;
  dirtyOwner?: string;
  isLoading: boolean;
  onNavigate: (route: ReportWorkspaceRoute) => void;
  onOpenAppendix: (sectionCode?: string) => void;
};

function ReportSectionTree({ sections, route, dirtyOwner, isLoading, onNavigate, onOpenAppendix }: ReportSectionTreeProps) {
  const orderedSections = useMemo(() => flattenSectionTree(sections), [sections]);

  function handleTreeKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!(["ArrowDown", "ArrowUp", "Home", "End"] as string[]).includes(event.key)) {
      return;
    }
    const buttons = Array.from(event.currentTarget.closest("nav")?.querySelectorAll<HTMLButtonElement>("button[data-tree-item]") ?? []);
    if (!buttons.length) {
      return;
    }
    event.preventDefault();
    const targetIndex = event.key === "Home"
      ? 0
      : event.key === "End"
        ? buttons.length - 1
        : event.key === "ArrowDown"
          ? Math.min(index + 1, buttons.length - 1)
          : Math.max(index - 1, 0);
    buttons[targetIndex]?.focus();
  }

  return (
    <nav className="report-section-tree" aria-label="完整报告章节树">
      <div className="report-tree-heading">
        <div>
          <p className="eyebrow">章节目录</p>
          <strong>母版章节</strong>
        </div>
        {dirtyOwner ? <span className="dirty-chip">未保存</span> : null}
      </div>
      <button
        type="button"
        data-tree-item
        className={route.view === "overview" ? "report-tree-button active" : "report-tree-button"}
        aria-current={route.view === "overview" ? "page" : undefined}
        onClick={() => onNavigate({ view: "overview" })}
        onKeyDown={(event) => handleTreeKeyDown(event, 0)}
      >
        <span>概览</span>
        <small>项目基础数据</small>
      </button>
      <button
        type="button"
        data-tree-item
        className={route.view === "basics" ? "report-tree-button active" : "report-tree-button"}
        aria-current={route.view === "basics" ? "page" : undefined}
        onClick={() => onNavigate({ view: "basics" })}
        onKeyDown={(event) => handleTreeKeyDown(event, 1)}
      >
        <span>基础数据</span>
        <small>单位、人员、日期与系统画像</small>
      </button>
      <button
        type="button"
        data-tree-item
        className={route.view === "objects" ? "report-tree-button active" : "report-tree-button"}
        aria-current={route.view === "objects" ? "page" : undefined}
        onClick={() => onNavigate({ view: "objects" })}
        onKeyDown={(event) => handleTreeKeyDown(event, 2)}
      >
        <span>测评对象库</span>
        <small>对象与引用关系</small>
      </button>
      {isLoading ? <p className="report-tree-loading">正在读取...</p> : null}
      {orderedSections.map((section, sectionIndex) => {
        const active = route.view === "section" && route.sectionKey === section.section_key;
        return (
          <button
            type="button"
            data-tree-item
            key={section.section_uuid}
            className={active ? "report-tree-button active" : "report-tree-button"}
            style={{ "--tree-level": Math.max(section.level - 1, 0) } as React.CSSProperties}
            aria-current={active ? "page" : undefined}
            onClick={() => section.section_type === "appendix_a"
              ? onOpenAppendix(appendixCodeFromSection(section))
              : onNavigate({ view: "section", sectionKey: section.section_key })}
            onKeyDown={(event) => handleTreeKeyDown(event, sectionIndex + 3)}
          >
            <span>{section.title}</span>
            <small>{completionLabel(section.completion_status)}</small>
          </button>
        );
      })}
    </nav>
  );
}

type ReportOverviewPanelProps = {
  projectUuid: string;
  overview?: ReportOverview;
  sections: ReportSection[];
  onDirtyChange: (dirty: boolean) => void;
  onNavigate: (route: ReportWorkspaceRoute) => void;
  onSaved: () => void;
};

function ReportOverviewPanel({ projectUuid, overview, sections, onDirtyChange, onNavigate, onSaved }: ReportOverviewPanelProps) {
  return (
    <div className="report-page-stack">
      <section className="report-page-heading">
        <p className="eyebrow">报告概览</p>
        <h3>从权威数据开始编写</h3>
        <p>章节、对象和校验状态均来自当前项目绑定的母版与报告 API。</p>
      </section>
      <div className="report-metric-grid" aria-label="报告完成度摘要">
        <ReportMetric label="章节完成" value={overview ? `${overview.completed_section_count}/${overview.section_count}` : "—"} />
        <ReportMetric label="测评对象" value={overview ? String(overview.object_count) : "—"} />
        <ReportMetric label="未绑定 A 行" value={overview ? String(overview.unbound_assessment_row_count) : "—"} />
        <ReportMetric label="校验问题" value={overview ? `${overview.error_count} 错误 / ${overview.warning_count} 警告` : "—"} />
      </div>
      <div className="report-overview-actions">
        <button type="button" onClick={() => onNavigate({ view: "basics" })}>完善基础数据</button>
        <button type="button" className="secondary-button" onClick={() => onNavigate({ view: "objects" })}>管理测评对象</button>
        {sections[0] ? (
          <button type="button" onClick={() => onNavigate({ view: "section", sectionKey: sections[0].section_key })}>开始编写章节</button>
        ) : null}
      </div>
      <ReportMetadataForm
        projectUuid={projectUuid}
        onDirtyChange={onDirtyChange}
        onSaved={onSaved}
      />
    </div>
  );
}

function ReportMetric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function ReportMetadataForm({
  projectUuid,
  onDirtyChange,
  onSaved
}: {
  projectUuid: string;
  onDirtyChange: (dirty: boolean) => void;
  onSaved: () => void;
}) {
  const [serverValue, setServerValue] = useState<ReportMetadata>();
  const [reportNumber, setReportNumber] = useState("");
  const [exportVersion, setExportVersion] = useState("V1.0");
  const [classificationLevel, setClassificationLevel] = useState("三级");
  const [confidentialityLevel, setConfidentialityLevel] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();
  const [conflict, setConflict] = useState<ConflictState>();
  const [numberAvailability, setNumberAvailability] = useState<ReportNumberAvailability>();
  const [isCheckingNumber, setIsCheckingNumber] = useState(false);

  const dirty = serverValue
    ? reportNumber !== serverValue.report_number
      || exportVersion !== serverValue.default_export_version
      || classificationLevel !== (serverValue.classification_level ?? "三级")
      || confidentialityLevel !== (serverValue.confidentiality_level ?? "")
    : false;

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(undefined);
    setServerValue(undefined);
    try {
      const metadata = await getReportMetadata(projectUuid);
      setServerValue(metadata);
      setReportNumber(metadata.report_number ?? "");
      setExportVersion(metadata.default_export_version ?? "V1.0");
      setClassificationLevel(metadata.classification_level ?? "三级");
      setConfidentialityLevel(metadata.confidentiality_level ?? "");
      setConflict(undefined);
      setNumberAvailability(undefined);
      onDirtyChange(false);
    } catch (loadError) {
      setError(errorMessage(loadError, "读取报告基础信息失败"));
    } finally {
      setIsLoading(false);
    }
  }, [onDirtyChange, projectUuid]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => {
    const value = reportNumber.trim();
    if (!value) {
      setNumberAvailability({ report_number: "", available: false, duplicate_project_count: 0, empty: true });
      setIsCheckingNumber(false);
      return;
    }
    let cancelled = false;
    setIsCheckingNumber(true);
    const timer = window.setTimeout(() => {
      void getReportNumberAvailability(projectUuid, value)
        .then((result) => { if (!cancelled) setNumberAvailability(result); })
        .catch(() => { if (!cancelled) setNumberAvailability(undefined); })
        .finally(() => { if (!cancelled) setIsCheckingNumber(false); });
    }, 350);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [projectUuid, reportNumber]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!serverValue || !dirty) {
      return;
    }
    setIsSaving(true);
    setError(undefined);
    setMessage(undefined);
    try {
      const saved = await updateReportMetadata(projectUuid, serverValue.revision, {
        report_number: reportNumber.trim(),
        default_export_version: exportVersion.trim() || "V1.0",
        classification_level: classificationLevel.trim() || "三级",
        confidentiality_level: confidentialityLevel.trim(),
        compiler_member_uuid: serverValue.compiler_member_uuid ?? null,
        reviewer_member_uuid: serverValue.reviewer_member_uuid ?? null,
        approver_member_uuid: serverValue.approver_member_uuid ?? null,
        controlled_extension: serverValue.controlled_extension ?? {}
      });
      setServerValue(saved);
      setReportNumber(saved.report_number ?? "");
      setExportVersion(saved.default_export_version ?? "V1.0");
      setClassificationLevel(saved.classification_level ?? "三级");
      setConfidentialityLevel(saved.confidentiality_level ?? "");
      setConflict(undefined);
      onDirtyChange(false);
      setMessage("报告基础信息已保存，项目状态已按后端规则同步。");
      onSaved();
    } catch (saveError) {
      if (isRevisionConflict(saveError)) {
        setConflict(conflictFromError(saveError));
      } else {
        setError(errorMessage(saveError, "保存报告基础信息失败"));
      }
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <section className="report-form-card" aria-labelledby="report-metadata-heading">
      <div className="report-card-heading">
        <div>
          <p className="eyebrow">基础数据</p>
          <h4 id="report-metadata-heading">报告身份</h4>
        </div>
        <span className={dirty ? "dirty-chip" : "clean-chip"}>{dirty ? "未保存" : "已保存"}</span>
      </div>
      {isLoading ? <p className="report-loading">正在读取基础信息...</p> : null}
      {conflict ? (
        <div className="revision-conflict" role="alert">
          <strong>服务器内容已更新，本地草稿尚未覆盖。</strong>
          <p>{conflict.message}{typeof conflict.currentRevision === "number" ? `（服务器 revision ${conflict.currentRevision}）` : ""}</p>
          <button type="button" className="secondary-button" onClick={load}>放弃本地草稿并刷新</button>
        </div>
      ) : null}
      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="success" aria-live="polite">{message}</p> : null}
      {!isLoading && serverValue ? (
        <form className="report-basic-form" onSubmit={handleSubmit}>
          <label>
            <span>系统名称（权威来源）</span>
            <input value={serverValue.system_name ?? "尚未填写"} readOnly aria-readonly="true" />
          </label>
          <label>
            <span>报告编号</span>
            <input value={reportNumber} onChange={(event) => setReportNumber(event.target.value)} maxLength={120} />
            <small className={numberAvailability?.available ? "report-field-valid" : numberAvailability && !numberAvailability.empty ? "report-field-invalid" : ""}>
              {isCheckingNumber ? "正在检查编号..." : numberAvailability?.empty ? "进入复核前必须填写。" : numberAvailability?.available ? "当前编号可用。" : numberAvailability ? `与 ${numberAvailability.duplicate_project_count} 个其他项目重复，提交复核时将被阻断。` : "暂未取得查重结果。"}
            </small>
          </label>
          <label>
            <span>默认导出版本</span>
            <input value={exportVersion} onChange={(event) => setExportVersion(event.target.value)} maxLength={24} />
          </label>
          <label>
            <span>系统安全保护等级</span>
            <input value={classificationLevel} onChange={(event) => setClassificationLevel(event.target.value)} maxLength={40} />
          </label>
          <label>
            <span>报告密级</span>
            <input value={confidentialityLevel} onChange={(event) => setConfidentialityLevel(event.target.value)} maxLength={40} />
          </label>
          <div className="report-form-actions">
            <button type="submit" disabled={!dirty || isSaving}>{isSaving ? "保存中..." : "保存基础信息"}</button>
          </div>
        </form>
      ) : null}
    </section>
  );
}

type ObjectSubsystemDraft = {
  subsystem_name: string;
  methods: AssessmentMethod[];
  remark: string;
  expected_revision?: number;
};

type NewObjectDraft = {
  object_type: AssessmentObjectType;
  name_snapshot: string;
  source_section_code: string;
  properties_text: string;
};

type NewRelationDraft = {
  source_object_uuid: string;
  target_object_uuid: string;
  relation_type: ObjectRelationType;
  properties_text: string;
};

type NewCorrectionDraft = {
  a2_object_uuid: string;
  a4_object_uuid: string;
  correction_kind: CorrectionKind;
  original_references_text: string;
};

function ReportObjectLibrary({
  projectUuid,
  onDirtyChange,
  onChanged
}: {
  projectUuid: string;
  onDirtyChange: (dirty: boolean) => void;
  onChanged: () => void;
}) {
  const [objects, setObjects] = useState<AssessmentObject[]>([]);
  const [objectDrafts, setObjectDrafts] = useState<AssessmentObject[]>([]);
  const [objectPropertiesText, setObjectPropertiesText] = useState<Record<string, string>>({});
  const [subsystemDrafts, setSubsystemDrafts] = useState<Record<string, ObjectSubsystemDraft>>({});
  const [applicationCatalog, setApplicationCatalog] = useState<string[]>([]);
  const [relations, setRelations] = useState<ObjectRelation[]>([]);
  const [relationDrafts, setRelationDrafts] = useState<ObjectRelation[]>([]);
  const [relationPropertiesText, setRelationPropertiesText] = useState<Record<string, string>>({});
  const [corrections, setCorrections] = useState<CorrectionRelation[]>([]);
  const [correctionDrafts, setCorrectionDrafts] = useState<CorrectionRelation[]>([]);
  const [correctionReferencesText, setCorrectionReferencesText] = useState<Record<string, string>>({});
  const [bindingPreview, setBindingPreview] = useState<BindingPreview>();
  const [bindingChoices, setBindingChoices] = useState<Record<number, string>>({});
  const [duplicateGroups, setDuplicateGroups] = useState<DuplicateObjectGroup[]>([]);
  const [mergeSelections, setMergeSelections] = useState<Record<string, { source: string; target: string }>>({});
  const [keyword, setKeyword] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [newObject, setNewObject] = useState<NewObjectDraft>({ object_type: "physical", name_snapshot: "", source_section_code: "", properties_text: "{}" });
  const [newRelation, setNewRelation] = useState<NewRelationDraft>({ source_object_uuid: "", target_object_uuid: "", relation_type: "connects", properties_text: "{}" });
  const [newCorrection, setNewCorrection] = useState<NewCorrectionDraft>({ a2_object_uuid: "", a4_object_uuid: "", correction_kind: "confidentiality", original_references_text: "{}" });
  const [isLoading, setIsLoading] = useState(true);
  const [savingId, setSavingId] = useState<string>();
  const [isPreviewing, setIsPreviewing] = useState(false);
  const [isScanningDuplicates, setIsScanningDuplicates] = useState(false);
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();
  const [conflict, setConflict] = useState<ConflictState>();

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(undefined);
    try {
      const [nextObjects, nextRelations, nextCorrections, profile] = await Promise.all([
        listAssessmentObjects(projectUuid), listObjectRelations(projectUuid),
        listCorrectionRelations(projectUuid), getReportSystemProfile(projectUuid)
      ]);
      setObjects(nextObjects);
      setObjectDrafts(nextObjects.map((item) => ({ ...item, properties: { ...item.properties }, methods: [...item.methods] })));
      setObjectPropertiesText(Object.fromEntries(nextObjects.map((item) => [item.object_uuid, prettyJson(item.properties)])));
      setSubsystemDrafts(Object.fromEntries(nextObjects.filter((item) => item.source_section_code === "A-4").map((item) => [
        item.object_uuid,
        { subsystem_name: item.subsystem_name ?? "", methods: [...item.methods], remark: item.remark, expected_revision: item.subsystem_revision ?? undefined }
      ])));
      setApplicationCatalog(profile.application_catalog);
      setRelations(nextRelations);
      setRelationDrafts(nextRelations.map((item) => ({ ...item, properties: { ...item.properties } })));
      setRelationPropertiesText(Object.fromEntries(nextRelations.map((item) => [item.relation_uuid, prettyJson(item.properties)])));
      setCorrections(nextCorrections);
      setCorrectionDrafts(nextCorrections.map((item) => ({ ...item, original_references: { ...item.original_references } })));
      setCorrectionReferencesText(Object.fromEntries(nextCorrections.map((item) => [item.correction_uuid, prettyJson(item.original_references)])));
      setBindingPreview(undefined);
      setBindingChoices({});
      setDuplicateGroups([]);
      setMergeSelections({});
      setNewObject({ object_type: "physical", name_snapshot: "", source_section_code: "", properties_text: "{}" });
      setNewRelation({ source_object_uuid: "", target_object_uuid: "", relation_type: "connects", properties_text: "{}" });
      setNewCorrection({ a2_object_uuid: "", a4_object_uuid: "", correction_kind: "confidentiality", original_references_text: "{}" });
      setConflict(undefined);
    } catch (loadError) {
      setError(errorMessage(loadError, "读取测评对象与关系失败"));
    } finally {
      setIsLoading(false);
    }
  }, [projectUuid]);

  useEffect(() => { void load(); }, [load]);

  const objectRowsDirty = objectDrafts.some((draft) => {
    const server = objects.find((item) => item.object_uuid === draft.object_uuid);
    return !server || !sameData(objectWriteShape(draft), objectWriteShape(server))
      || (objectPropertiesText[draft.object_uuid] ?? "") !== prettyJson(server.properties);
  });
  const subsystemRowsDirty = Object.entries(subsystemDrafts).some(([objectUuid, draft]) => {
    const server = objects.find((item) => item.object_uuid === objectUuid);
    return Boolean(server) && !sameData(
      { subsystem_name: draft.subsystem_name, methods: draft.methods, remark: draft.remark },
      { subsystem_name: server?.subsystem_name ?? "", methods: server?.methods ?? [], remark: server?.remark ?? "" }
    );
  });
  const relationRowsDirty = relationDrafts.some((draft) => {
    const server = relations.find((item) => item.relation_uuid === draft.relation_uuid);
    return !server || !sameData(relationWriteShape(draft), relationWriteShape(server))
      || (relationPropertiesText[draft.relation_uuid] ?? "") !== prettyJson(server.properties);
  });
  const correctionRowsDirty = correctionDrafts.some((draft) => {
    const server = corrections.find((item) => item.correction_uuid === draft.correction_uuid);
    return !server || !sameData(correctionWriteShape(draft), correctionWriteShape(server))
      || (correctionReferencesText[draft.correction_uuid] ?? "") !== prettyJson(server.original_references);
  });
  const dirty = objectRowsDirty || subsystemRowsDirty || relationRowsDirty || correctionRowsDirty
    || Boolean(newObject.name_snapshot.trim() || newObject.source_section_code || newObject.properties_text.trim() !== "{}")
    || Boolean(newRelation.source_object_uuid || newRelation.target_object_uuid || newRelation.properties_text.trim() !== "{}")
    || Boolean(newCorrection.a2_object_uuid || newCorrection.a4_object_uuid || newCorrection.original_references_text.trim() !== "{}")
    || Object.values(bindingChoices).some(Boolean)
    || Object.values(mergeSelections).some((item) => Boolean(item.source || item.target));
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);

  const visibleObjects = useMemo(() => objectDrafts.filter((object) => {
    const matchesKeyword = !keyword.trim() || [object.name_snapshot, object.subsystem_name, object.source_section_code]
      .some((value) => value?.toLowerCase().includes(keyword.trim().toLowerCase()));
    return matchesKeyword && (!typeFilter || object.object_type === typeFilter);
  }), [keyword, objectDrafts, typeFilter]);
  const a2Objects = objects.filter((item) => item.active && item.source_section_code === "A-2");
  const a4Objects = objects.filter((item) => item.active && item.source_section_code === "A-4");

  function setOperationError(operationError: unknown, fallback: string) {
    if (isRevisionConflict(operationError)) setConflict(conflictFromError(operationError));
    else setError(errorMessage(operationError, fallback));
  }

  function updateObjectDraft(objectUuid: string, patch: Partial<AssessmentObject>) {
    setObjectDrafts((current) => current.map((item) => item.object_uuid === objectUuid ? { ...item, ...patch } : item));
    if (patch.source_section_code === "A-4") {
      setSubsystemDrafts((current) => current[objectUuid] ? current : {
        ...current,
        [objectUuid]: { subsystem_name: "", methods: [], remark: "" }
      });
    }
    setMessage(undefined);
  }

  async function handleCreateObject(event: FormEvent) {
    event.preventDefault();
    if (!newObject.name_snapshot.trim()) return;
    setSavingId("new-object"); setError(undefined); setConflict(undefined);
    try {
      const properties = parseFlatJsonObject(newObject.properties_text, "对象扩展属性");
      const payload: AssessmentObjectInput = {
        object_type: newObject.object_type, name_snapshot: newObject.name_snapshot.trim(),
        source_section_code: newObject.source_section_code || null, source_row_id: null, properties, active: true
      };
      await createAssessmentObject(projectUuid, payload);
      setMessage("测评对象已新增。"); await load(); onChanged();
    } catch (saveError) { setOperationError(saveError, "创建测评对象失败"); }
    finally { setSavingId(undefined); }
  }

  async function saveObject(objectUuid: string) {
    const draft = objectDrafts.find((item) => item.object_uuid === objectUuid);
    if (!draft) return;
    setSavingId(objectUuid); setError(undefined); setConflict(undefined);
    try {
      const saved = await updateAssessmentObject(projectUuid, {
        ...draft, name_snapshot: draft.name_snapshot.trim(),
        properties: parseFlatJsonObject(objectPropertiesText[objectUuid] ?? "{}", "对象扩展属性")
      });
      setObjects((current) => current.map((item) => item.object_uuid === objectUuid ? saved : item));
      setObjectDrafts((current) => current.map((item) => item.object_uuid === objectUuid ? saved : item));
      setObjectPropertiesText((current) => ({ ...current, [objectUuid]: prettyJson(saved.properties) }));
      if (saved.source_section_code === "A-4") {
        setSubsystemDrafts((current) => current[objectUuid] ? current : {
          ...current,
          [objectUuid]: {
            subsystem_name: saved.subsystem_name ?? "",
            methods: [...saved.methods],
            remark: saved.remark,
            expected_revision: saved.subsystem_revision ?? undefined
          }
        });
      }
      setMessage(`对象“${saved.name_snapshot}”已保存。`); onChanged();
    } catch (saveError) { setOperationError(saveError, "保存测评对象失败"); }
    finally { setSavingId(undefined); }
  }

  async function removeObject(object: AssessmentObject) {
    if (!window.confirm(`确定删除“${object.name_snapshot}”吗？若仍被附录 A、关系、修正或章节引用，后端会阻止删除；可先停用对象。`)) return;
    setSavingId(object.object_uuid); setError(undefined); setConflict(undefined);
    try {
      await deleteAssessmentObject(projectUuid, object);
      setMessage(`对象“${object.name_snapshot}”已删除。`); await load(); onChanged();
    } catch (deleteError) { setOperationError(deleteError, "删除测评对象失败"); }
    finally { setSavingId(undefined); }
  }

  async function saveSubsystem(object: AssessmentObject) {
    const draft = subsystemDrafts[object.object_uuid];
    if (!draft?.subsystem_name) return;
    setSavingId(`subsystem:${object.object_uuid}`); setError(undefined); setConflict(undefined);
    try {
      const saved = await upsertObjectSubsystem(projectUuid, { object_uuid: object.object_uuid, ...draft });
      const patch = { subsystem_name: saved.subsystem_name, methods: saved.methods, remark: saved.remark, subsystem_revision: saved.revision };
      setObjects((current) => current.map((item) => item.object_uuid === object.object_uuid ? { ...item, ...patch } : item));
      setObjectDrafts((current) => current.map((item) => item.object_uuid === object.object_uuid ? { ...item, ...patch } : item));
      setSubsystemDrafts((current) => ({ ...current, [object.object_uuid]: { ...draft, expected_revision: saved.revision } }));
      setMessage(`A-4 对象“${object.name_snapshot}”的子系统已保存。`); onChanged();
    } catch (saveError) { setOperationError(saveError, "保存 A-4 子系统失败"); }
    finally { setSavingId(undefined); }
  }

  async function loadBindingPreview() {
    setIsPreviewing(true); setError(undefined);
    try { setBindingPreview(await previewAppendixBindings(projectUuid)); setBindingChoices({}); }
    catch (previewError) { setError(errorMessage(previewError, "生成附录 A 绑定预览失败")); }
    finally { setIsPreviewing(false); }
  }

  async function confirmBindings() {
    const choices = Object.entries(bindingChoices).filter(([, objectUuid]) => Boolean(objectUuid))
      .map(([sourceRowId, objectUuid]) => ({ source_row_id: Number(sourceRowId), object_uuid: objectUuid }));
    if (!choices.length) { setError("请至少明确选择一条附录 A 绑定。"); return; }
    setSavingId("bindings"); setError(undefined);
    try {
      const saved = await confirmAppendixBindings(projectUuid, choices);
      setMessage(`已确认 ${saved.bound_count} 条附录 A 对象绑定。`); await load(); onChanged();
    } catch (saveError) { setOperationError(saveError, "确认附录 A 绑定失败"); }
    finally { setSavingId(undefined); }
  }

  async function scanDuplicates() {
    setIsScanningDuplicates(true); setError(undefined);
    try { setDuplicateGroups(await listDuplicateObjectCandidates(projectUuid)); setMergeSelections({}); }
    catch (scanError) { setError(errorMessage(scanError, "扫描重复对象失败")); }
    finally { setIsScanningDuplicates(false); }
  }

  async function mergeGroup(group: DuplicateObjectGroup) {
    const key = duplicateGroupKey(group);
    const selected = mergeSelections[key];
    const source = objects.find((item) => item.object_uuid === selected?.source);
    const target = objects.find((item) => item.object_uuid === selected?.target);
    if (!source || !target || source.object_uuid === target.object_uuid) { setError("请明确选择不同的来源对象和保留对象。"); return; }
    if (!window.confirm(`确定把“${source.name_snapshot}”合并到“${target.name_snapshot}”吗？此操作会迁移引用并删除来源对象。`)) return;
    setSavingId(`merge:${key}`); setError(undefined); setConflict(undefined);
    try { await mergeAssessmentObjects(projectUuid, source, target); setMessage("重复对象已显式合并。"); await load(); onChanged(); }
    catch (mergeError) { setOperationError(mergeError, "合并测评对象失败"); }
    finally { setSavingId(undefined); }
  }

  function updateRelationDraft(relationUuid: string, patch: Partial<ObjectRelation>) {
    setRelationDrafts((current) => current.map((item) => item.relation_uuid === relationUuid ? { ...item, ...patch } : item));
  }

  async function createRelation(event: FormEvent) {
    event.preventDefault();
    if (!newRelation.source_object_uuid || !newRelation.target_object_uuid || newRelation.source_object_uuid === newRelation.target_object_uuid) return;
    setSavingId("new-relation"); setError(undefined);
    try {
      const payload: ObjectRelationInput = {
        source_object_uuid: newRelation.source_object_uuid,
        target_object_uuid: newRelation.target_object_uuid,
        relation_type: newRelation.relation_type,
        properties: parseFlatJsonObject(newRelation.properties_text, "对象关系属性"),
        active: true
      };
      await createObjectRelation(projectUuid, payload);
      setMessage("对象关系已新增。"); await load(); onChanged();
    } catch (saveError) { setOperationError(saveError, "新增对象关系失败"); }
    finally { setSavingId(undefined); }
  }

  async function saveRelation(relationUuid: string) {
    const draft = relationDrafts.find((item) => item.relation_uuid === relationUuid);
    if (!draft) return;
    setSavingId(relationUuid); setError(undefined); setConflict(undefined);
    try {
      const saved = await updateObjectRelation(projectUuid, { ...draft, properties: parseFlatJsonObject(relationPropertiesText[relationUuid] ?? "{}", "对象关系属性") });
      setRelations((current) => current.map((item) => item.relation_uuid === relationUuid ? saved : item));
      setRelationDrafts((current) => current.map((item) => item.relation_uuid === relationUuid ? saved : item));
      setRelationPropertiesText((current) => ({ ...current, [relationUuid]: prettyJson(saved.properties) }));
      setMessage("对象关系已保存。"); onChanged();
    } catch (saveError) { setOperationError(saveError, "保存对象关系失败"); }
    finally { setSavingId(undefined); }
  }

  async function removeRelation(relation: ObjectRelation) {
    if (!window.confirm("确定删除该对象关系吗？")) return;
    setSavingId(relation.relation_uuid); setError(undefined); setConflict(undefined);
    try { await deleteObjectRelation(projectUuid, relation); setMessage("对象关系已删除。"); await load(); onChanged(); }
    catch (deleteError) { setOperationError(deleteError, "删除对象关系失败"); }
    finally { setSavingId(undefined); }
  }

  function correctionPatch(kind: CorrectionKind) {
    const pair = CORRECTION_METRIC_PAIRS[kind];
    return { correction_kind: kind, a2_metric_code: pair.a2, a4_metric_code: pair.a4 };
  }

  function updateCorrectionDraft(correctionUuid: string, patch: Partial<CorrectionRelation>) {
    setCorrectionDrafts((current) => current.map((item) => item.correction_uuid === correctionUuid ? { ...item, ...patch } : item));
  }

  async function createCorrection(event: FormEvent) {
    event.preventDefault();
    if (!newCorrection.a2_object_uuid || !newCorrection.a4_object_uuid) return;
    setSavingId("new-correction"); setError(undefined);
    try {
      const pair = CORRECTION_METRIC_PAIRS[newCorrection.correction_kind];
      const payload: CorrectionRelationInput = {
        a2_object_uuid: newCorrection.a2_object_uuid, a4_object_uuid: newCorrection.a4_object_uuid,
        correction_kind: newCorrection.correction_kind, a2_metric_code: pair.a2, a4_metric_code: pair.a4,
        original_references: parseReferenceJson(newCorrection.original_references_text, "原始分值引用")
      };
      await createCorrectionRelation(projectUuid, payload);
      setMessage("A-2/A-4 修正关系已新增。"); await load(); onChanged();
    } catch (saveError) { setOperationError(saveError, "新增修正关系失败"); }
    finally { setSavingId(undefined); }
  }

  async function saveCorrection(correctionUuid: string) {
    const draft = correctionDrafts.find((item) => item.correction_uuid === correctionUuid);
    if (!draft) return;
    setSavingId(correctionUuid); setError(undefined); setConflict(undefined);
    try {
      const saved = await updateCorrectionRelation(projectUuid, {
        ...draft, original_references: parseReferenceJson(correctionReferencesText[correctionUuid] ?? "{}", "原始分值引用")
      });
      setCorrections((current) => current.map((item) => item.correction_uuid === correctionUuid ? saved : item));
      setCorrectionDrafts((current) => current.map((item) => item.correction_uuid === correctionUuid ? saved : item));
      setCorrectionReferencesText((current) => ({ ...current, [correctionUuid]: prettyJson(saved.original_references) }));
      setMessage("A-2/A-4 修正关系已保存。"); onChanged();
    } catch (saveError) { setOperationError(saveError, "保存修正关系失败"); }
    finally { setSavingId(undefined); }
  }

  async function removeCorrection(relation: CorrectionRelation) {
    if (!window.confirm("确定删除该 A-2/A-4 修正关系吗？")) return;
    setSavingId(relation.correction_uuid); setError(undefined); setConflict(undefined);
    try { await deleteCorrectionRelation(projectUuid, relation); setMessage("修正关系已删除。"); await load(); onChanged(); }
    catch (deleteError) { setOperationError(deleteError, "删除修正关系失败"); }
    finally { setSavingId(undefined); }
  }

  function bindingObjectOptions(item: BindingPreviewItem) {
    const preferred = new Set(item.matches.map((match) => match.object_uuid));
    const expectedType = SECTION_OBJECT_TYPES[item.section_code];
    return [...objects]
      .filter((object) => object.active && (!expectedType || object.object_type === expectedType))
      .sort((first, second) => Number(preferred.has(second.object_uuid)) - Number(preferred.has(first.object_uuid))
        || first.name_snapshot.localeCompare(second.name_snapshot, "zh-CN"));
  }

  const bindingGroups: Array<{ key: keyof BindingPreview; label: string }> = [
    { key: "exact", label: "精确匹配" },
    { key: "candidate", label: "候选匹配" },
    { key: "ambiguous", label: "多候选" },
    { key: "unmatched", label: "未匹配" }
  ];

  return (
    <div className="report-page-stack">
      <section className="report-page-heading">
        <p className="eyebrow">中央对象库</p>
        <h3>测评对象与引用</h3>
        <p>对象以稳定 UUID 复用；名称仅用于显示，不进行自动合并。</p>
      </section>
      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="success" aria-live="polite">{message}</p> : null}
      {conflict ? <div className="revision-conflict" role="alert"><strong>版本冲突，本地草稿仍保留。</strong><p>{conflict.message}</p><button type="button" className="secondary-button" onClick={load}>放弃对象工作区草稿并刷新</button></div> : null}

      <form className="report-form-card report-object-create" onSubmit={handleCreateObject}>
        <div className="report-form-card-heading"><div><strong>新增测评对象</strong><small>人工创建的对象不会自动与同名对象合并。</small></div></div>
        <div className="report-field-grid">
          <label><span>来源章节</span><select value={newObject.source_section_code} onChange={(event) => {
            const source = event.target.value;
            setNewObject((current) => ({ ...current, source_section_code: source, object_type: SECTION_OBJECT_TYPES[source] ?? current.object_type }));
          }}><option value="">人工创建</option>{appendixSectionOptions()}</select></label>
          <label><span>对象类型</span><select value={newObject.object_type} onChange={(event) => setNewObject((current) => ({ ...current, object_type: event.target.value as AssessmentObjectType }))}>{OBJECT_TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
          <label className="report-field-wide"><span>对象名称</span><input value={newObject.name_snapshot} onChange={(event) => setNewObject((current) => ({ ...current, name_snapshot: event.target.value }))} maxLength={240} required /></label>
          <label className="report-field-wide"><span>扩展属性（扁平 JSON）</span><textarea rows={3} value={newObject.properties_text} onChange={(event) => setNewObject((current) => ({ ...current, properties_text: event.target.value }))} /></label>
        </div>
        <div className="report-inline-actions"><button type="submit" disabled={!newObject.name_snapshot.trim() || savingId === "new-object"}>{savingId === "new-object" ? "创建中..." : "新增对象"}</button></div>
      </form>

      <div className="report-object-filters" aria-label="对象筛选">
        <label><span>关键词</span><input value={keyword} onChange={(event) => setKeyword(event.target.value)} placeholder="名称、子系统或来源章节" /></label>
        <label><span>类型</span><select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}><option value="">全部类型</option>{OBJECT_TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
      </div>
      {isLoading ? <p className="report-loading">正在读取对象...</p> : null}
      {!isLoading && visibleObjects.length === 0 ? <p className="report-empty-state">当前筛选条件下没有对象。</p> : null}
      <div className="report-object-list">
        {visibleObjects.map((object) => (
          <article key={object.object_uuid} className="report-object-card report-repeat-card">
            <div className="report-form-card-heading"><div><strong>{object.name_snapshot || "未命名对象"}</strong><small>{objectTypeLabel(object.object_type)} · 引用 {object.reference_count} 处 · 版本 {object.revision}</small></div><span className={object.active ? "clean-chip" : "dirty-chip"}>{object.active ? "启用" : "已停用"}</span></div>
            <div className="report-field-grid">
              <label className="report-field-wide"><span>对象名称</span><input value={object.name_snapshot} onChange={(event) => updateObjectDraft(object.object_uuid, { name_snapshot: event.target.value })} /></label>
              <label><span>来源章节</span><select disabled={object.source_row_id != null} value={object.source_section_code ?? ""} onChange={(event) => {
                const source = event.target.value;
                updateObjectDraft(object.object_uuid, { source_section_code: source || null, object_type: SECTION_OBJECT_TYPES[source] ?? object.object_type });
              }}><option value="">人工创建</option>{appendixSectionOptions()}</select></label>
              <label><span>对象类型</span><select disabled={object.source_row_id != null} value={object.object_type} onChange={(event) => updateObjectDraft(object.object_uuid, { object_type: event.target.value })}>{OBJECT_TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
              <label className="report-field-wide"><span>扩展属性（扁平 JSON）</span><textarea rows={3} value={objectPropertiesText[object.object_uuid] ?? "{}"} onChange={(event) => setObjectPropertiesText((current) => ({ ...current, [object.object_uuid]: event.target.value }))} /></label>
              <label className="report-checkbox-line"><input type="checkbox" checked={object.active} onChange={(event) => updateObjectDraft(object.object_uuid, { active: event.target.checked })} /><span>启用对象（取消勾选即停用）</span></label>
            </div>
            <div className="report-inline-actions"><button type="button" onClick={() => saveObject(object.object_uuid)} disabled={!object.name_snapshot.trim() || savingId === object.object_uuid}>保存对象</button><button type="button" className="danger-button" onClick={() => removeObject(object)} disabled={savingId === object.object_uuid}>删除对象</button></div>
            {object.source_section_code === "A-4" ? <div className="report-nested-card">
              <strong>A-4 子系统、测评方式与备注</strong>
              <label><span>所属子系统</span><select value={subsystemDrafts[object.object_uuid]?.subsystem_name ?? ""} onChange={(event) => setSubsystemDrafts((current) => ({ ...current, [object.object_uuid]: { ...(current[object.object_uuid] ?? { methods: [], remark: "" }), subsystem_name: event.target.value } }))}><option value="">请选择应用名称</option>{applicationCatalog.map((name) => <option key={name} value={name}>{name}</option>)}</select></label>
              <fieldset className="report-check-grid"><legend>测评方式</legend>{ASSESSMENT_METHODS.map((method) => <label key={method}><input type="checkbox" checked={subsystemDrafts[object.object_uuid]?.methods.includes(method) ?? false} onChange={(event) => setSubsystemDrafts((current) => {
                const draft = current[object.object_uuid] ?? { subsystem_name: "", methods: [], remark: "" };
                const methods = event.target.checked ? [...draft.methods, method] : draft.methods.filter((item) => item !== method);
                return { ...current, [object.object_uuid]: { ...draft, methods } };
              })} />{method}</label>)}</fieldset>
              <label><span>备注</span><textarea rows={2} value={subsystemDrafts[object.object_uuid]?.remark ?? ""} onChange={(event) => setSubsystemDrafts((current) => ({ ...current, [object.object_uuid]: { ...(current[object.object_uuid] ?? { subsystem_name: "", methods: [] }), remark: event.target.value } }))} /></label>
              <button type="button" className="secondary-button" disabled={!subsystemDrafts[object.object_uuid]?.subsystem_name || savingId === `subsystem:${object.object_uuid}`} onClick={() => saveSubsystem(object)}>保存 A-4 子系统</button>
            </div> : null}
          </article>
        ))}
      </div>

      <section className="report-form-card">
        <div className="report-form-card-heading"><div><strong>附录 A 绑定预览</strong><small>只生成建议，不自动绑定；必须逐条明确选择后确认。</small></div><button type="button" className="secondary-button" onClick={loadBindingPreview} disabled={isPreviewing}>{isPreviewing ? "生成中..." : "生成绑定预览"}</button></div>
        {bindingPreview ? bindingGroups.map((group) => bindingPreview[group.key].length ? <div className="report-binding-group" key={group.key}><h4>{group.label}（{bindingPreview[group.key].length}）</h4>{bindingPreview[group.key].map((item) => <div className="report-field-row" key={`${group.key}:${item.source_row_id}`}><span>{item.section_code} · 记录 #{item.source_row_id} · {item.object_name}{item.subsystem ? ` · ${item.subsystem}` : ""}</span><select aria-label={`${item.object_name} 绑定对象`} value={bindingChoices[item.source_row_id] ?? ""} onChange={(event) => setBindingChoices((current) => ({ ...current, [item.source_row_id]: event.target.value }))}><option value="">不绑定</option>{bindingObjectOptions(item).map((candidate) => <option key={candidate.object_uuid} value={candidate.object_uuid}>{candidate.name_snapshot}{item.matches.some((match) => match.object_uuid === candidate.object_uuid) ? "（建议）" : ""}</option>)}</select></div>)}</div> : null) : <p>尚未生成预览。</p>}
        {bindingPreview ? <div className="report-inline-actions"><button type="button" onClick={confirmBindings} disabled={!Object.values(bindingChoices).some(Boolean) || savingId === "bindings"}>确认选中绑定</button></div> : null}
      </section>

      <section className="report-form-card">
        <div className="report-form-card-heading"><div><strong>重复对象与显式合并</strong><small>扫描只给出候选；合并前必须明确选择来源对象和保留对象。</small></div><button type="button" className="secondary-button" onClick={scanDuplicates} disabled={isScanningDuplicates}>{isScanningDuplicates ? "扫描中..." : "扫描重复对象"}</button></div>
        {!duplicateGroups.length ? <p>当前没有已加载的重复候选。</p> : duplicateGroups.map((group) => {
          const key = duplicateGroupKey(group); const selected = mergeSelections[key] ?? { source: "", target: "" };
          return <div className="report-repeat-card" key={key}><strong>{objectTypeLabel(group.object_type)} · {group.normalized_name}</strong><div className="report-field-row"><label><span>来源对象（将删除）</span><select value={selected.source} onChange={(event) => setMergeSelections((current) => ({ ...current, [key]: { ...selected, source: event.target.value } }))}><option value="">请选择</option>{group.objects.map((item) => <option key={item.object_uuid} value={item.object_uuid}>{item.name_snapshot} · 引用 {item.reference_count}</option>)}</select></label><label><span>保留对象</span><select value={selected.target} onChange={(event) => setMergeSelections((current) => ({ ...current, [key]: { ...selected, target: event.target.value } }))}><option value="">请选择</option>{group.objects.map((item) => <option key={item.object_uuid} value={item.object_uuid}>{item.name_snapshot} · 引用 {item.reference_count}</option>)}</select></label><button type="button" onClick={() => mergeGroup(group)} disabled={!selected.source || !selected.target || selected.source === selected.target || savingId === `merge:${key}`}>显式合并</button></div></div>;
        })}
      </section>

      <section className="report-form-card">
        <div className="report-form-card-heading"><div><strong>对象关系</strong><small>维护对象之间的稳定 UUID 关系。</small></div></div>
        {relationDrafts.map((relation) => <div className="report-repeat-card" key={relation.relation_uuid}><div className="report-field-grid"><label><span>来源对象</span><ObjectSelect objects={objectDrafts} value={relation.source_object_uuid} onChange={(value) => updateRelationDraft(relation.relation_uuid, { source_object_uuid: value })} /></label><label><span>关系</span><select value={relation.relation_type} onChange={(event) => updateRelationDraft(relation.relation_uuid, { relation_type: event.target.value as ObjectRelationType })}>{OBJECT_RELATION_OPTIONS.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label><label><span>目标对象</span><ObjectSelect objects={objectDrafts} value={relation.target_object_uuid} onChange={(value) => updateRelationDraft(relation.relation_uuid, { target_object_uuid: value })} /></label><label className="report-field-wide"><span>关系属性（扁平 JSON）</span><textarea rows={2} value={relationPropertiesText[relation.relation_uuid] ?? "{}"} onChange={(event) => setRelationPropertiesText((current) => ({ ...current, [relation.relation_uuid]: event.target.value }))} /></label><label className="report-checkbox-line"><input type="checkbox" checked={relation.active} onChange={(event) => updateRelationDraft(relation.relation_uuid, { active: event.target.checked })} /><span>启用关系</span></label></div><div className="report-inline-actions"><button type="button" onClick={() => saveRelation(relation.relation_uuid)} disabled={!relation.source_object_uuid || !relation.target_object_uuid || relation.source_object_uuid === relation.target_object_uuid || savingId === relation.relation_uuid}>保存关系</button><button type="button" className="danger-button" onClick={() => removeRelation(relation)}>删除关系</button></div></div>)}
        <form className="report-repeat-card" onSubmit={createRelation}><strong>新增对象关系</strong><div className="report-field-grid"><label><span>来源对象</span><ObjectSelect objects={objects.filter((item) => item.active)} value={newRelation.source_object_uuid} onChange={(value) => setNewRelation((current) => ({ ...current, source_object_uuid: value }))} /></label><label><span>关系</span><select value={newRelation.relation_type} onChange={(event) => setNewRelation((current) => ({ ...current, relation_type: event.target.value as ObjectRelationType }))}>{OBJECT_RELATION_OPTIONS.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label><label><span>目标对象</span><ObjectSelect objects={objects.filter((item) => item.active)} value={newRelation.target_object_uuid} onChange={(value) => setNewRelation((current) => ({ ...current, target_object_uuid: value }))} /></label><label className="report-field-wide"><span>关系属性（扁平 JSON）</span><textarea rows={2} value={newRelation.properties_text} onChange={(event) => setNewRelation((current) => ({ ...current, properties_text: event.target.value }))} /></label></div><button type="submit" disabled={!newRelation.source_object_uuid || !newRelation.target_object_uuid || newRelation.source_object_uuid === newRelation.target_object_uuid || savingId === "new-relation"}>新增关系</button></form>
      </section>

      <section className="report-form-card">
        <div className="report-form-card-heading"><div><strong>A-2/A-4 修正关系</strong><small>仅建立合法指标对之间的原始结果关联；最终修正由后端规则计算。</small></div></div>
        {correctionDrafts.map((relation) => <div className="report-repeat-card" key={relation.correction_uuid}><div className="report-field-grid"><label><span>A-2 网络通道</span><ObjectSelect objects={a2Objects} value={relation.a2_object_uuid} onChange={(value) => updateCorrectionDraft(relation.correction_uuid, { a2_object_uuid: value })} /></label><label><span>修正类型</span><select value={relation.correction_kind} onChange={(event) => updateCorrectionDraft(relation.correction_uuid, correctionPatch(event.target.value as CorrectionKind))}>{Object.entries(CORRECTION_METRIC_PAIRS).map(([kind, pair]) => <option key={kind} value={kind}>{pair.label}</option>)}</select></label><label><span>A-4 应用对象</span><ObjectSelect objects={a4Objects} value={relation.a4_object_uuid} onChange={(value) => updateCorrectionDraft(relation.correction_uuid, { a4_object_uuid: value })} /></label><p className="report-field-wide">指标对：{relation.a2_metric_code} ↔ {relation.a4_metric_code}</p><label className="report-field-wide"><span>原始分值引用（JSON）</span><textarea rows={3} value={correctionReferencesText[relation.correction_uuid] ?? "{}"} onChange={(event) => setCorrectionReferencesText((current) => ({ ...current, [relation.correction_uuid]: event.target.value }))} /></label></div><div className="report-inline-actions"><button type="button" onClick={() => saveCorrection(relation.correction_uuid)} disabled={!relation.a2_object_uuid || !relation.a4_object_uuid || savingId === relation.correction_uuid}>保存修正关系</button><button type="button" className="danger-button" onClick={() => removeCorrection(relation)}>删除修正关系</button></div></div>)}
        <form className="report-repeat-card" onSubmit={createCorrection}><strong>新增 A-2/A-4 修正关系</strong><div className="report-field-grid"><label><span>A-2 网络通道</span><ObjectSelect objects={a2Objects} value={newCorrection.a2_object_uuid} onChange={(value) => setNewCorrection((current) => ({ ...current, a2_object_uuid: value }))} /></label><label><span>修正类型</span><select value={newCorrection.correction_kind} onChange={(event) => setNewCorrection((current) => ({ ...current, correction_kind: event.target.value as CorrectionKind }))}>{Object.entries(CORRECTION_METRIC_PAIRS).map(([kind, pair]) => <option key={kind} value={kind}>{pair.label}</option>)}</select></label><label><span>A-4 应用对象</span><ObjectSelect objects={a4Objects} value={newCorrection.a4_object_uuid} onChange={(value) => setNewCorrection((current) => ({ ...current, a4_object_uuid: value }))} /></label><p className="report-field-wide">指标对：{CORRECTION_METRIC_PAIRS[newCorrection.correction_kind].a2} ↔ {CORRECTION_METRIC_PAIRS[newCorrection.correction_kind].a4}</p><label className="report-field-wide"><span>原始分值引用（JSON）</span><textarea rows={3} value={newCorrection.original_references_text} onChange={(event) => setNewCorrection((current) => ({ ...current, original_references_text: event.target.value }))} /></label></div><button type="submit" disabled={!newCorrection.a2_object_uuid || !newCorrection.a4_object_uuid || savingId === "new-correction"}>新增修正关系</button></form>
      </section>
    </div>
  );
}

function ReportSectionWorkspace({
  projectUuid,
  section,
  onDirtyChange,
  onOpenAppendix,
  onSaved
}: {
  projectUuid: string;
  section: ReportSection;
  onDirtyChange: (dirty: boolean) => void;
  onOpenAppendix: (sectionCode?: string) => void;
  onSaved: () => void;
}) {
  const [detail, setDetail] = useState<ReportSectionDetail>();
  const [sectionState, setSectionState] = useState<ReportSection>();
  const [blocks, setBlocks] = useState<ReportBlock[]>([]);
  const [dirtyIds, setDirtyIds] = useState<Set<string>>(new Set());
  const [invalidBlockIds, setInvalidBlockIds] = useState<Set<string>>(new Set());
  const [completionStatus, setCompletionStatus] = useState<"not_started" | "in_progress" | "complete">("not_started");
  const [conflict, setConflict] = useState<ConflictState>();
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isAdding, setIsAdding] = useState(false);
  const [deletingId, setDeletingId] = useState<string>();
  const [newBlockType, setNewBlockType] = useState<ReportBlockType>("paragraph");
  const [newBlockTarget, setNewBlockTarget] = useState("");
  const [newBlockLabel, setNewBlockLabel] = useState("");
  const [error, setError] = useState<string>();
  const [message, setMessage] = useState<string>();

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(undefined);
    setDetail(undefined);
    setSectionState(undefined);
    setBlocks([]);
    setDirtyIds(new Set());
    setInvalidBlockIds(new Set());
    try {
      const loaded = await getReportSection(projectUuid, section.section_uuid);
      setDetail(loaded);
      setSectionState(loaded.section);
      setCompletionStatus(normalizeCompletionStatus(loaded.section.completion_status));
      setBlocks(loaded.blocks);
      setDirtyIds(new Set());
      setConflict(undefined);
      onDirtyChange(false);
    } catch (loadError) {
      setError(errorMessage(loadError, "读取章节内容失败"));
    } finally {
      setIsLoading(false);
    }
  }, [onDirtyChange, projectUuid, section.section_uuid]);

  useEffect(() => { void load(); }, [load]);
  const completionDirty = Boolean(sectionState && normalizeCompletionStatus(sectionState.completion_status) !== completionStatus);
  const hasDirtyContent = dirtyIds.size > 0 || invalidBlockIds.size > 0 || completionDirty;
  useEffect(() => { onDirtyChange(hasDirtyContent); }, [hasDirtyContent, onDirtyChange]);

  function updatePayload(blockUuid: string, payload: ReportBlockPayload) {
    setBlocks((current) => current.map((block) => block.block_uuid === blockUuid ? { ...block, payload } : block));
    setDirtyIds((current) => new Set(current).add(blockUuid));
    setMessage(undefined);
  }

  async function handleSave() {
    if (invalidBlockIds.size) {
      setError("存在格式不正确的结构化 JSON，请修正后再保存章节。");
      return;
    }
    if (!dirtyIds.size && !completionDirty) {
      return;
    }
    setIsSaving(true);
    setError(undefined);
    setMessage(undefined);
    setConflict(undefined);
    const savedById = new Map<string, ReportBlock>();
    let savedSection: ReportSection | undefined;
    try {
      for (const block of blocks.filter((item) => dirtyIds.has(item.block_uuid))) {
        savedById.set(block.block_uuid, await updateReportBlock(projectUuid, block));
      }
      if (completionDirty && sectionState) {
        savedSection = await updateReportSection(projectUuid, sectionState, completionStatus);
      }
      setBlocks((current) => current.map((block) => savedById.get(block.block_uuid) ?? block));
      if (savedSection) {
        setSectionState(savedSection);
        setCompletionStatus(normalizeCompletionStatus(savedSection.completion_status));
        setDetail((current) => current ? { ...current, section: savedSection as ReportSection } : current);
      }
      setDirtyIds(new Set());
      onDirtyChange(false);
      setMessage("章节内容已保存。");
      onSaved();
    } catch (saveError) {
      if (savedById.size) {
        setBlocks((current) => current.map((block) => savedById.get(block.block_uuid) ?? block));
        setDirtyIds((current) => new Set([...current].filter((blockUuid) => !savedById.has(blockUuid))));
      }
      if (isRevisionConflict(saveError)) {
        setConflict(conflictFromError(saveError));
      } else {
        setError(errorMessage(saveError, "保存章节内容失败"));
      }
    } finally {
      setIsSaving(false);
    }
  }

  const allowedNewTypes = section.allowed_block_types.filter((type) => EDITABLE_BLOCK_TYPES.includes(type));
  const selectedNewBlockType = allowedNewTypes.includes(newBlockType) ? newBlockType : allowedNewTypes[0];
  const newBlockNeedsTarget = selectedNewBlockType === "figure" || selectedNewBlockType === "reference";

  async function handleAddBlock() {
    if (!selectedNewBlockType) {
      return;
    }
    setIsAdding(true);
    setError(undefined);
    try {
      const payload = initialBlockPayload(selectedNewBlockType, newBlockTarget.trim(), newBlockLabel.trim());
      const created = await createReportBlock(projectUuid, section.section_uuid, selectedNewBlockType, payload);
      setBlocks((current) => [...current, created]);
      setNewBlockTarget("");
      setNewBlockLabel("");
      setMessage("已新增结构化块。");
      onSaved();
    } catch (saveError) {
      setError(errorMessage(saveError, "新增结构化块失败"));
    } finally {
      setIsAdding(false);
    }
  }

  async function removeBlock(block: ReportBlock) {
    if (hasDirtyContent) {
      setError("删除结构化块前请先保存或放弃当前章节草稿，以免刷新时丢失编辑内容。");
      return;
    }
    if (!window.confirm(`确定删除结构化块“${block.block_key}”吗？如仍被引用，后端会阻止删除。`)) return;
    setDeletingId(block.block_uuid);
    setError(undefined);
    setConflict(undefined);
    try {
      await deleteReportBlock(projectUuid, block);
      await load();
      setMessage("结构化块已删除。");
      onSaved();
    } catch (deleteError) {
      if (isRevisionConflict(deleteError)) setConflict(conflictFromError(deleteError));
      else setError(errorMessage(deleteError, "删除结构化块失败"));
    } finally {
      setDeletingId(undefined);
    }
  }

  if (section.section_type === "appendix_a") {
    return <div className="report-empty-state"><h3>{section.title}</h3><p>该章节继续使用现有附录 A 编辑器。</p><button type="button" onClick={() => onOpenAppendix()}>进入附录 A</button></div>;
  }

  return (
    <div className="report-page-stack">
      <section className="report-page-heading">
        <p className="eyebrow">{section.section_key}</p>
        <h3>{section.title}</h3>
        <p>{section.section_type === "generated" ? "该章节由后续派生阶段生成，当前仅显示输入状态。" : "内容按结构化块保存，版式由冻结母版控制。"}</p>
      </section>
      {conflict ? <div className="revision-conflict" role="alert"><strong>保存冲突，本地草稿仍保留。</strong><p>{conflict.message}</p><button type="button" className="secondary-button" onClick={load}>放弃本地草稿并读取服务器版本</button></div> : null}
      {error ? <p className="error" role="alert">{error}</p> : null}
      {message ? <p className="success" aria-live="polite">{message}</p> : null}
      <div className="report-section-toolbar">
        <span className={hasDirtyContent ? "dirty-chip" : "clean-chip"}>{hasDirtyContent ? `${dirtyIds.size} 个块或章节状态未保存` : "已保存"}</span>
        {section.edit_policy !== "readonly" && section.section_type !== "generated" ? <label className="report-completion-select"><span>章节完成状态</span><select value={completionStatus} onChange={(event) => setCompletionStatus(event.target.value as "not_started" | "in_progress" | "complete")}><option value="not_started">未开始</option><option value="in_progress">进行中</option><option value="complete">已完成</option></select></label> : null}
        {allowedNewTypes.length ? <div className="report-add-block"><label><span>新增类型</span><select value={selectedNewBlockType} onChange={(event) => { setNewBlockType(event.target.value as ReportBlockType); setNewBlockTarget(""); setNewBlockLabel(""); }}>{allowedNewTypes.map((type) => <option key={type} value={type}>{blockTypeLabel(type)}</option>)}</select></label>{newBlockNeedsTarget ? <><label><span>{selectedNewBlockType === "figure" ? "证据图片 UUID" : "引用目标 UUID"}</span><input value={newBlockTarget} onChange={(event) => setNewBlockTarget(event.target.value)} /></label><label><span>{selectedNewBlockType === "figure" ? "题注（可选）" : "显示标签（可选）"}</span><input value={newBlockLabel} onChange={(event) => setNewBlockLabel(event.target.value)} /></label></> : null}<button type="button" className="secondary-button" onClick={handleAddBlock} disabled={isAdding || (newBlockNeedsTarget && !newBlockTarget.trim())}>{isAdding ? "新增中..." : "新增块"}</button></div> : null}
        <button type="button" onClick={handleSave} disabled={!hasDirtyContent || isSaving || invalidBlockIds.size > 0}>{isSaving ? "保存中..." : "保存章节"}</button>
      </div>
      {isLoading ? <p className="report-loading">正在读取章节内容...</p> : null}
      {!isLoading && !blocks.length ? <p className="report-empty-state">该章节当前没有可编辑块。</p> : null}
      <div className="report-block-list">
        {blocks.map((block) => <ReportBlockEditor key={block.block_uuid} block={block} dirty={dirtyIds.has(block.block_uuid) || invalidBlockIds.has(block.block_uuid)} deleting={deletingId === block.block_uuid} onDelete={block.source_kind === "manual" && block.edit_policy !== "readonly" && block.baseline_kind !== "template_default" ? () => removeBlock(block) : undefined} onChange={(payload) => updatePayload(block.block_uuid, payload)} onValidityChange={(valid) => setInvalidBlockIds((current) => {
          const next = new Set(current); if (valid) next.delete(block.block_uuid); else next.add(block.block_uuid); return next;
        })} />)}
      </div>
      {detail?.issues.length ? <ReportIssueList issues={detail.issues} title="本章节校验问题" /> : null}
    </div>
  );
}

function ReportBlockEditor({
  block,
  dirty,
  deleting,
  onDelete,
  onChange,
  onValidityChange
}: {
  block: ReportBlock;
  dirty: boolean;
  deleting: boolean;
  onDelete?: () => void;
  onChange: (payload: ReportBlockPayload) => void;
  onValidityChange: (valid: boolean) => void;
}) {
  const editable = block.edit_policy !== "readonly" && block.source_kind !== "derived";
  const heading = <div className="report-block-heading"><div><strong>{blockTypeLabel(block.block_type)}</strong><small>{block.block_key}</small></div><div className="report-inline-actions"><span className={dirty ? "dirty-chip" : "clean-chip"}>{dirty ? "未保存" : editable ? "可编辑" : "只读"}</span>{onDelete ? <button type="button" className="danger-button" disabled={deleting || dirty} onClick={onDelete}>{deleting ? "删除中..." : "删除块"}</button> : null}</div></div>;

  if (block.block_type === "paragraph") {
    const payload = block.payload as ParagraphBlockPayload;
    return <article className="report-block-card">{heading}<textarea aria-label={`${block.block_key} 内容`} rows={6} value={payload.text ?? ""} readOnly={!editable} onChange={(event) => onChange({ text: event.target.value })} /></article>;
  }
  if (block.block_type === "bullet_list" || block.block_type === "numbered_list") {
    const payload = block.payload as ListBlockPayload;
    return <article className="report-block-card">{heading}<textarea aria-label={`${block.block_key} 列表项`} rows={6} value={(payload.items ?? []).join("\n")} readOnly={!editable} onChange={(event) => onChange({ items: event.target.value.split(/\r?\n/).filter((item) => item.trim().length > 0) })} /><small>每行保存为一个列表项。</small></article>;
  }
  if (block.block_type === "key_value_table" || block.block_type === "data_table") {
    return <article className="report-block-card">{heading}<JsonPayloadEditor block={block} editable={editable} onChange={onChange} onValidityChange={onValidityChange} /></article>;
  }
  if (block.block_type === "figure") {
    const payload = block.payload as FigureBlockPayload;
    return <article className="report-block-card">{heading}<div className="report-field-grid"><label><span>证据图片 UUID</span><input value={payload.figure_uuid ?? ""} readOnly={!editable} onChange={(event) => onChange({ ...payload, figure_uuid: event.target.value })} /></label><label><span>题注（可选）</span><input value={payload.caption ?? ""} readOnly={!editable} onChange={(event) => onChange({ ...payload, caption: event.target.value || null })} /></label></div></article>;
  }
  if (block.block_type === "reference") {
    const payload = block.payload as ReferenceBlockPayload;
    return <article className="report-block-card">{heading}<div className="report-field-grid"><label><span>引用目标 UUID</span><input value={payload.target_uuid ?? ""} readOnly={!editable} onChange={(event) => onChange({ ...payload, target_uuid: event.target.value })} /></label><label><span>显示标签（可选）</span><input value={payload.label ?? ""} readOnly={!editable} onChange={(event) => onChange({ ...payload, label: event.target.value || null })} /></label></div></article>;
  }
  if (block.block_type === "generated") {
    return <article className="report-block-card readonly">{heading}<p>待后续派生阶段生成。当前块不接受人工正文。</p></article>;
  }
  return <article className="report-block-card readonly">{heading}<p>当前母版返回了尚未支持的块类型，已按只读方式保留原始数据。</p></article>;
}

function JsonPayloadEditor({
  block,
  editable,
  onChange,
  onValidityChange
}: {
  block: ReportBlock;
  editable: boolean;
  onChange: (payload: ReportBlockPayload) => void;
  onValidityChange: (valid: boolean) => void;
}) {
  const [text, setText] = useState(() => prettyJson(block.payload));
  const [error, setError] = useState<string>();

  useEffect(() => {
    setText(prettyJson(block.payload));
    setError(undefined);
    onValidityChange(true);
    // revision 变化表示保存或重新读取后的服务器版本，届时才重置编辑文本。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [block.revision]);

  function handleChange(value: string) {
    setText(value);
    try {
      const parsed = parseStructuredBlockJson(block.block_type, value);
      setError(undefined);
      onValidityChange(true);
      onChange(parsed);
    } catch (parseError) {
      setError(errorMessage(parseError, "JSON 格式不正确"));
      onValidityChange(false);
    }
  }

  return <><textarea aria-label={`${block.block_key} 结构化 JSON`} rows={10} value={text} readOnly={!editable} onChange={(event) => handleChange(event.target.value)} />{error ? <small className="error" role="alert">{error}</small> : <small>{block.block_type === "key_value_table" ? "格式：{ rows: [{ key, value }] }" : "格式：{ schema_version, columns: [{ key, label }], rows: [...] }"}</small>}</>;
}

function ReportContextPanel({
  overview,
  validation,
  activeSection,
  pendingTemplateHintCount
}: {
  overview?: ReportOverview;
  validation?: ReportValidation;
  activeSection?: ReportSection;
  pendingTemplateHintCount?: number;
}) {
  return (
    <aside className="report-context-panel" aria-label="当前章节数据与校验摘要">
      <section><p className="eyebrow">当前上下文</p><h4>{activeSection?.title ?? "报告项目"}</h4><dl><div><dt>字段矩阵</dt><dd>{overview?.field_matrix_version ?? "读取中"}</dd></div><div><dt>母版</dt><dd>{overview?.template_revision ?? "读取中"}</dd></div></dl></section>
      <section><p className="eyebrow">校验摘要</p><div className="report-context-counts"><span className="error-count">{validation?.errors ?? overview?.error_count ?? "—"} 错误</span><span className="warning-count">{validation?.warnings ?? overview?.warning_count ?? "—"} 警告</span></div>{validation ? (validation.issues.length ? <ReportIssueList issues={validation.issues.slice(0, 6)} /> : <p>当前没有可显示的校验问题。</p>) : <p>校验摘要暂不可用。</p>}</section>
      <section>
        <p className="eyebrow">母版批注提示</p>
        <strong>{pendingTemplateHintCount ?? "—"} 条待审批</strong>
        <p>这些脱敏提示只用于来源核对和编写帮助，不会阻断提交复核。</p>
      </section>
    </aside>
  );
}

function ReportIssueList({ issues, title }: { issues: ReportIssue[]; title?: string }) {
  return <div className="report-issue-panel">{title ? <strong>{title}</strong> : null}<ul>{issues.map((issue, index) => <li className={issue.severity} key={`${issue.code}-${issue.entity_uuid ?? index}`}><span>{issue.severity === "error" ? "错误" : issue.severity === "warning" ? "警告" : "提示"}</span><p>{issue.message}<small>{issue.relation_id ?? issue.code}</small></p></li>)}</ul></div>;
}

function flattenSectionTree(sections: ReportSection[]): ReportSection[] {
  const children = new Map<string | null, ReportSection[]>();
  sections.forEach((section) => {
    const parent = section.parent_section_uuid ?? null;
    children.set(parent, [...(children.get(parent) ?? []), section]);
  });
  children.forEach((items) => items.sort((first, second) => first.sort_order - second.sort_order));
  const flattened: ReportSection[] = [];
  const visit = (parent: string | null) => {
    (children.get(parent) ?? []).forEach((section) => {
      flattened.push(section);
      visit(section.section_uuid);
    });
  };
  visit(null);
  if (flattened.length !== sections.length) {
    const seen = new Set(flattened.map((section) => section.section_uuid));
    flattened.push(...sections.filter((section) => !seen.has(section.section_uuid)).sort((first, second) => first.sort_order - second.sort_order));
  }
  return flattened;
}

function routeFromLocation(projectUuid: string): ReportWorkspaceRoute {
  const parsed = parseProjectWorkspacePath(window.location.pathname);
  if (!parsed || parsed.projectUuid !== projectUuid || parsed.route.view === "appendix_a") {
    return { view: "overview" };
  }
  return parsed.route;
}

function sameRoute(first: ReportWorkspaceRoute, second: ReportWorkspaceRoute): boolean {
  if (first.view !== second.view) return false;
  if (first.view === "section" && second.view === "section") return first.sectionKey === second.sectionKey;
  if (first.view === "appendix_a" && second.view === "appendix_a") return first.sectionCode === second.sectionCode;
  return true;
}

function completionLabel(status: ReportSection["completion_status"]): string {
  if (status === "complete" || status === "completed") return "已完成";
  if (status === "in_progress") return "编写中";
  return "未开始";
}

function appendixCodeFromSection(section: ReportSection): string | undefined {
  const match = section.section_key.match(/\.a([1-8])$/i);
  return match ? `A-${match[1]}` : undefined;
}

function blockTypeLabel(type: string): string {
  const labels: Record<string, string> = { paragraph: "段落", bullet_list: "项目列表", numbered_list: "编号列表", key_value_table: "键值表", data_table: "数据表", figure: "图片", reference: "引用", generated: "生成内容" };
  return labels[type] ?? `暂不支持：${type}`;
}

function objectTypeLabel(type: string): string {
  return OBJECT_TYPE_OPTIONS.find((option) => option.value === type)?.label ?? type;
}

function appendixSectionOptions() {
  return Object.keys(SECTION_OBJECT_TYPES).map((sectionCode) => <option key={sectionCode} value={sectionCode}>{sectionCode}</option>);
}

function ObjectSelect({
  objects,
  value,
  onChange
}: {
  objects: AssessmentObject[];
  value: string;
  onChange: (value: string) => void;
}) {
  return <select value={value} onChange={(event) => onChange(event.target.value)}><option value="">请选择对象</option>{objects.map((object) => <option key={object.object_uuid} value={object.object_uuid}>{object.name_snapshot}（{object.source_section_code || objectTypeLabel(object.object_type)}）{object.active ? "" : "（已停用）"}</option>)}</select>;
}

function prettyJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

function sameData(first: unknown, second: unknown): boolean {
  return JSON.stringify(first) === JSON.stringify(second);
}

function objectWriteShape(object: AssessmentObject) {
  return {
    object_type: object.object_type,
    name_snapshot: object.name_snapshot,
    source_section_code: object.source_section_code ?? null,
    source_row_id: object.source_row_id ?? null,
    active: object.active
  };
}

function relationWriteShape(relation: ObjectRelation) {
  return {
    source_object_uuid: relation.source_object_uuid,
    target_object_uuid: relation.target_object_uuid,
    relation_type: relation.relation_type,
    active: relation.active
  };
}

function correctionWriteShape(relation: CorrectionRelation) {
  return {
    a2_object_uuid: relation.a2_object_uuid,
    a4_object_uuid: relation.a4_object_uuid,
    correction_kind: relation.correction_kind,
    a2_metric_code: relation.a2_metric_code,
    a4_metric_code: relation.a4_metric_code
  };
}

function parseJsonRecord(value: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error(`${label}必须是合法 JSON。`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`${label}必须是 JSON 对象。`);
  }
  return parsed as Record<string, unknown>;
}

function parseFlatJsonObject(value: string, label: string): AssessmentObject["properties"] {
  const parsed = parseJsonRecord(value, label);
  for (const [key, item] of Object.entries(parsed)) {
    if (!key.trim() || (item !== null && !["string", "number", "boolean"].includes(typeof item))) {
      throw new Error(`${label}仅允许非空键及字符串、数字、布尔值或 null。`);
    }
  }
  return parsed as AssessmentObject["properties"];
}

function parseReferenceJson(value: string, label: string): CorrectionRelation["original_references"] {
  const parsed = parseJsonRecord(value, label);
  const keys = Object.keys(parsed).sort();
  if (keys.length !== 2 || keys[0] !== "a2_row_id" || keys[1] !== "a4_row_id") {
    throw new Error(`${label}必须且只能包含 a2_row_id 和 a4_row_id。`);
  }
  const a2RowId = parsed.a2_row_id;
  const a4RowId = parsed.a4_row_id;
  if (!Number.isInteger(a2RowId) || Number(a2RowId) < 1 || !Number.isInteger(a4RowId) || Number(a4RowId) < 1) {
    throw new Error(`${label}中的 A-2、A-4 记录 ID 必须是正整数。`);
  }
  return { a2_row_id: Number(a2RowId), a4_row_id: Number(a4RowId) };
}

function duplicateGroupKey(group: DuplicateObjectGroup): string {
  return `${group.object_type}:${group.normalized_name}`;
}

function normalizeCompletionStatus(status: ReportCompletionStatus): "not_started" | "in_progress" | "complete" {
  return status === "completed" ? "complete" : status;
}

function initialBlockPayload(type: ReportBlockType, targetUuid: string, label: string): ReportBlockPayload {
  if (type === "paragraph") return { text: "" };
  if (type === "bullet_list" || type === "numbered_list") return { items: [] };
  if (type === "key_value_table") return { rows: [] };
  if (type === "data_table") return { schema_version: "1", columns: [{ key: "column_1", label: "列 1" }], rows: [] };
  if (type === "figure") return { figure_uuid: targetUuid, caption: label || null };
  if (type === "reference") return { target_uuid: targetUuid, label: label || null };
  return { status: "not_generated" };
}

function parseStructuredBlockJson(type: string, value: string): ReportBlockPayload {
  const parsed = parseJsonRecord(value, blockTypeLabel(type));
  if (type === "key_value_table") {
    if (!Array.isArray(parsed.rows) || !parsed.rows.every((row) => row && typeof row === "object" && !Array.isArray(row)
      && typeof (row as Record<string, unknown>).key === "string" && typeof (row as Record<string, unknown>).value === "string")) {
      throw new Error("键值表 rows 必须由 { key, value } 字符串对象组成。");
    }
    return parsed as KeyValueBlockPayload;
  }
  if (type === "data_table") {
    if (typeof parsed.schema_version !== "string"
      || !Array.isArray(parsed.columns)
      || !parsed.columns.length
      || !parsed.columns.every((column) => column && typeof column === "object" && !Array.isArray(column)
        && typeof (column as Record<string, unknown>).key === "string" && typeof (column as Record<string, unknown>).label === "string")
      || !Array.isArray(parsed.rows)
      || !parsed.rows.every((row) => row && typeof row === "object" && !Array.isArray(row)
        && Object.values(row as Record<string, unknown>).every((cell) => typeof cell === "string"))) {
      throw new Error("数据表必须包含 schema_version、columns 和字符串单元格 rows。 ");
    }
    return parsed as DataTableBlockPayload;
  }
  throw new Error("该块类型不支持 JSON 编辑。");
}

function isRevisionConflict(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 409 && error.code === "REVISION_CONFLICT";
}

function conflictFromError(error: ApiError): ConflictState {
  const details = error.details && typeof error.details === "object" ? error.details as Record<string, unknown> : {};
  return { message: error.message, currentRevision: typeof details.current_revision === "number" ? details.current_revision : undefined };
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}
