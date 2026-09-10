import { ReactNode } from "react";

export function Stamp({ children, tone = "ink", animate = false }: { children: ReactNode; tone?: "ink" | "accent"; animate?: boolean }) {
  return <span className={`stamp stamp-${tone}${animate ? " stamp-in" : ""}`}>{children}</span>;
}

export function Tag({ children, active = false }: { children: ReactNode; active?: boolean }) {
  return <span className={`tag${active ? " tag-active" : ""}`}>{children}</span>;
}

export function Stat({ label, value, note }: { label: string; value: ReactNode; note?: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {note ? <span className="stat-note">{note}</span> : null}
    </div>
  );
}

export function Card({ title, children, className = "", aside }: { title?: string; children: ReactNode; className?: string; aside?: ReactNode }) {
  return (
    <div className={`card ${className}`}>
      {title ? (
        <div className="card-head">
          <h3>{title}</h3>
          {aside}
        </div>
      ) : null}
      {children}
    </div>
  );
}

export function Section({ id, num, title, lede, children }: { id: string; num: string; title: string; lede: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-title`} className="section">
      <div className="section-head">
        <span className="section-num" aria-hidden="true">{num}</span>
        <div>
          <h2 id={`${id}-title`}>{title}</h2>
          <p className="lede">{lede}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

export function Code({ children }: { children: ReactNode }) {
  return <code className="code">{children}</code>;
}
