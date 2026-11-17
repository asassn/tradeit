/**
 * The small pieces every view is assembled from.
 *
 * Deliberately few. A design system earns its keep by constraining choice,
 * and a library with four kinds of card has already lost — the reader learns
 * the vocabulary of the product by seeing the same shapes mean the same
 * things.
 *
 * **There is no Button that submits, and no component here can POST.** The
 * service is read-only by construction and the component library carries that
 * property rather than relying on nobody adding one: a dashboard that can
 * place an order is a trading system with a chart attached. A test asserts
 * the absence.
 */

import type { ReactNode } from "react";
import "./Primitives.css";

export function Card({
  title,
  action,
  children,
  flush,
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
  /** Remove body padding — for a table that should meet the card edge. */
  flush?: boolean;
}) {
  return (
    <section className="card">
      {title && (
        <header className="card__head">
          <h2 className="card__title">{title}</h2>
          {action}
        </header>
      )}
      <div className={flush ? "card__body card__body--flush" : "card__body"}>
        {children}
      </div>
    </section>
  );
}

/**
 * One figure, its label, and the context that stops it being misread.
 *
 * `context` is not decoration. A multiple without "dearer than 98% of its own
 * history" is a number with no scale, and the reader supplies a scale from
 * memory — usually the wrong one.
 */
export function Stat({
  label,
  value,
  context,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  context?: ReactNode;
  tone?: "neutral" | "warning" | "accent";
}) {
  return (
    <div className="stat">
      <div className="stat__label">{label}</div>
      <div className={`stat__value stat__value--${tone}`}>{value}</div>
      {context && <div className={`stat__context stat__context--${tone}`}>{context}</div>}
    </div>
  );
}

export function StatRow({ children }: { children: ReactNode }) {
  return <div className="stat-row">{children}</div>;
}

export function Pill({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "warning" | "critical" | "accent";
}) {
  return <span className={`pill pill--${tone}`}>{children}</span>;
}

export function PageHeader({
  title,
  subtitle,
  aside,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div className="page-head__main">
        <h1 className="page-head__title">{title}</h1>
        {subtitle && <div className="page-head__sub">{subtitle}</div>}
      </div>
      {aside && <div className="page-head__aside">{aside}</div>}
    </header>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="empty" role="status">
      {label}
      <span className="sr-only">, please wait</span>
    </div>
  );
}

/**
 * A table that scrolls inside its own container rather than widening the page.
 *
 * Measured on the previous dashboard at 375px: without this the last columns
 * sat off the right edge with nothing to indicate they were there.
 */
export function TableScroller({ children }: { children: ReactNode }) {
  return <div className="table-scroller">{children}</div>;
}
