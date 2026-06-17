import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  createProject,
  getSectionDetail,
  getTemplateProfile,
  updateSectionDetail,
  type AssessmentRowInput,
  type Project,
  type SectionDetail,
  type TemplateProfile
} from "../api/client";
import { AssessmentTable } from "../components/AssessmentTable";
import { Layout } from "../components/Layout";
import { SectionNav } from "../components/SectionNav";

function rowsFromDetail(detail: SectionDetail): AssessmentRowInput[] {
  return detail.rows.map((row) => ({
    unit: row.unit,
    object_name: row.object_name,
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
  const [activeCode, setActiveCode] = useState<string>();
  const [profile, setProfile] = useState<TemplateProfile | null>(null);
  const [sectionDetails, setSectionDetails] = useState<Record<string, SectionDetail>>({});
  const [draftRows, setDraftRows] = useState<Record<string, AssessmentRowInput[]>>({});
  const [dirtySections, setDirtySections] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string>();
  const [isCreating, setIsCreating] = useState(false);
  const [isLoadingSection, setIsLoadingSection] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string>();

  const activeSection = useMemo(
    () => project?.sections.find((section) => section.code === activeCode),
    [activeCode, project]
  );

  const activeDetail = activeCode ? sectionDetails[activeCode] : undefined;
  const activeRows = activeCode ? draftRows[activeCode] ?? [] : [];
  const isDirty = activeCode ? dirtySections.has(activeCode) : false;

  useEffect(() => {
    getTemplateProfile()
      .then(setProfile)
      .catch((err) => setError(err instanceof Error ? err.message : "读取模板 profile 失败"));
  }, []);

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

  async function handleCreateProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    setIsCreating(true);
    try {
      const created = await createProject(projectName);
      setProject(created);
      setSectionDetails({});
      setDraftRows({});
      setDirtySections(new Set());
      setSaveMessage(undefined);
      setActiveCode(created.sections[0]?.code);
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
      setSectionDetails((current) => ({ ...current, [activeCode]: detail }));
      setDraftRows((current) => ({ ...current, [activeCode]: rowsFromDetail(detail) }));
      setDirtySections((current) => {
        const next = new Set(current);
        next.delete(activeCode);
        return next;
      });
      setSaveMessage(`${activeCode} 已保存`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存章节失败");
    } finally {
      setIsSaving(false);
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
        <section className="panel">
          <h2>创建项目</h2>
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
          {error ? <p className="error">{error}</p> : null}
        </section>
      ) : (
        <section className="panel wide-panel">
          <p className="eyebrow">当前项目</p>
          <h2>{project.name}</h2>
          {activeSection ? (
            <div className="section-summary">
              <span className="section-code">{activeSection.code}</span>
              <div>
                <h3>{activeSection.title}</h3>
                <p>{activeSection.table_title}</p>
              </div>
            </div>
          ) : null}

          {error ? <p className="error">{error}</p> : null}
          {saveMessage ? <p className="success">{saveMessage}</p> : null}

          {isLoadingSection ? <p className="loading-text">正在读取章节...</p> : null}

          {profile && activeCode && activeDetail ? (
            <AssessmentTable
              sectionCode={activeCode}
              rows={activeRows}
              profile={profile}
              isSaving={isSaving}
              isDirty={isDirty}
              onRowsChange={(rows) => handleRowsChange(activeCode, rows)}
              onSave={handleSaveSection}
            />
          ) : null}
        </section>
      )}
    </Layout>
  );
}
