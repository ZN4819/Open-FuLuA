import { FormEvent, useMemo, useState } from "react";
import { createProject, type Project } from "../api/client";
import { Layout } from "../components/Layout";
import { SectionNav } from "../components/SectionNav";

export function ProjectPage() {
  const [projectName, setProjectName] = useState("附录A测评结果记录");
  const [project, setProject] = useState<Project | null>(null);
  const [activeCode, setActiveCode] = useState<string>();
  const [error, setError] = useState<string>();
  const [isCreating, setIsCreating] = useState(false);

  const activeSection = useMemo(
    () => project?.sections.find((section) => section.code === activeCode),
    [activeCode, project]
  );

  async function handleCreateProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(undefined);
    setIsCreating(true);
    try {
      const created = await createProject(projectName);
      setProject(created);
      setActiveCode(created.sections[0]?.code);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建项目失败");
    } finally {
      setIsCreating(false);
    }
  }

  return (
    <Layout
      title="附录A编写工具"
      sidebar={
        project ? (
          <SectionNav sections={project.sections} activeCode={activeCode} onSelect={setActiveCode} />
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
        <section className="panel">
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
          <div className="placeholder-grid">
            <div>
              <strong>测评表</strong>
              <p>下一阶段接入两类测评表编辑器。</p>
            </div>
            <div>
              <strong>证据图片</strong>
              <p>后续支持上传、排序、题注和引用。</p>
            </div>
            <div>
              <strong>预览导出</strong>
              <p>后续接入 DOCX 导出与异步预览。</p>
            </div>
          </div>
        </section>
      )}
    </Layout>
  );
}
