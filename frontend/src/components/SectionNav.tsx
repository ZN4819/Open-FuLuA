import type { Section } from "../api/client";

type SectionNavProps = {
  sections: Section[];
  activeCode?: string;
  dirtyCodes?: Set<string>;
  onSelect: (code: string) => void;
};

export function SectionNav({ sections, activeCode, dirtyCodes, onSelect }: SectionNavProps) {
  const dirtyCount = dirtyCodes?.size ?? 0;

  return (
    <nav className="section-nav" aria-label="附录A章节">
      <div className="section-nav-header">
        <div>
          <p className="eyebrow">章节目录</p>
          <strong>A-1 至 A-8</strong>
        </div>
        <span className={dirtyCount > 0 ? "dirty-chip" : "clean-chip"}>
          {dirtyCount > 0 ? `${dirtyCount} 未保存` : "已保存"}
        </span>
      </div>
      <div className="section-nav-list">
        {sections.map((section) => {
          const isActive = section.code === activeCode;
          const isDirty = dirtyCodes?.has(section.code) ?? false;

          return (
            <button
              key={section.code}
              aria-current={isActive ? "page" : undefined}
              className={isActive ? "section-nav-button active" : "section-nav-button"}
              type="button"
              onClick={() => onSelect(section.code)}
            >
              <span className="section-nav-code">{section.code}</span>
              <span className="section-nav-label">
                <strong>{section.title}</strong>
                <small>{section.table_title}</small>
              </span>
              {isDirty ? <em className="section-nav-state dirty">未保存</em> : isActive ? <em className="section-nav-state">当前</em> : null}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
