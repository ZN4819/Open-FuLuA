import type { Section } from "../api/client";

type SectionNavProps = {
  sections: Section[];
  activeCode?: string;
  onSelect: (code: string) => void;
};

export function SectionNav({ sections, activeCode, onSelect }: SectionNavProps) {
  return (
    <nav className="section-nav" aria-label="附录A章节">
      {sections.map((section) => (
        <button
          key={section.code}
          className={section.code === activeCode ? "active" : ""}
          type="button"
          onClick={() => onSelect(section.code)}
        >
          <span>{section.code}</span>
          <strong>{section.title}</strong>
        </button>
      ))}
    </nav>
  );
}
