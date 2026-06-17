import type { ReactNode } from "react";

type LayoutProps = {
  title: string;
  sidebar: ReactNode;
  children: ReactNode;
};

export function Layout({ title, sidebar, children }: LayoutProps) {
  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">本地 Web 应用</p>
          <h1>{title}</h1>
        </div>
      </header>
      <div className="workspace">
        <aside className="sidebar">{sidebar}</aside>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
