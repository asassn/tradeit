/**
 * ⌘K — the universal entry point.
 *
 * Justified rather than fashionable. The published rule of thumb is that a
 * command palette earns its place above roughly twenty navigation targets and
 * that a sidebar is simpler below it; this product addresses **41,698
 * securities** plus every view, so the palette is the navigation and the rail
 * is the map.
 *
 * It searches securities and views in the same list on purpose. A reader
 * types "AAPL" or "decisions" and should not have to know which kind of thing
 * they are looking for before they start typing.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, searchSecurities, type SearchHit } from "@/lib/api";
import { NAV } from "./nav";
import "./CommandPalette.css";

type Item =
  | { kind: "view"; id: string; label: string; hint: string; to: string }
  | { kind: "security"; id: string; hit: SearchHit };

export function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [cursor, setCursor] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const views: Item[] = NAV.flatMap((group) =>
    group.items.map((item) => ({
      kind: "view" as const,
      id: item.to,
      label: item.label,
      hint: group.label,
      to: item.to,
    })),
  );

  const term = query.trim().toLowerCase();
  const matchedViews = term
    ? views.filter((v) => v.kind === "view" && v.label.toLowerCase().includes(term))
    : views;
  const items: Item[] = [
    ...matchedViews,
    ...hits.map((hit) => ({
      kind: "security" as const,
      id: `sec-${hit.security_id}-${hit.ticker}`,
      hit,
    })),
  ];

  // Debounced, because a keystroke per request against a 73 GB corpus is a
  // self-inflicted outage.
  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    if (q.length < 1) {
      setHits([]);
      setError(null);
      return;
    }
    const timer = setTimeout(() => {
      searchSecurities(q)
        .then((d) => {
          setHits(d.results);
          setError(null);
        })
        .catch((e: unknown) => {
          setHits([]);
          setError(
            e instanceof ApiError && e.status === 501
              ? "No corpus attached — start the dashboard with --corpus to search securities."
              : e instanceof Error
                ? e.message
                : "Search failed",
          );
        });
    }, 160);
    return () => clearTimeout(timer);
  }, [query, open]);

  useEffect(() => {
    setCursor(0);
  }, [query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setHits([]);
      setError(null);
      // The input is only in the DOM once open; focus after paint.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const choose = useCallback(
    (item: Item | undefined) => {
      if (!item) return;
      navigate(
        item.kind === "view" ? item.to : `/securities/${item.hit.security_id}`,
      );
      onClose();
    },
    [navigate, onClose],
  );

  if (!open) return null;

  return (
    <div
      className="palette__scrim"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="palette" role="dialog" aria-modal="true" aria-label="Command palette">
        <input
          ref={inputRef}
          className="palette__input"
          placeholder="Search securities and views…"
          value={query}
          spellCheck={false}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              onClose();
            } else if (e.key === "ArrowDown") {
              e.preventDefault();
              setCursor((c) => (items.length ? (c + 1) % items.length : 0));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setCursor((c) =>
                items.length ? (c - 1 + items.length) % items.length : 0,
              );
            } else if (e.key === "Enter") {
              e.preventDefault();
              choose(items[cursor]);
            }
          }}
        />
        <div className="palette__list">
          {error && <div className="palette__note">{error}</div>}
          {!error && items.length === 0 && (
            <div className="palette__note">Nothing matches “{query}”.</div>
          )}
          {items.map((item, index) => (
            <button
              key={item.id}
              type="button"
              className={`palette__row${index === cursor ? " is-on" : ""}`}
              onMouseEnter={() => setCursor(index)}
              onClick={() => choose(item)}
            >
              {item.kind === "view" ? (
                <>
                  <span className="palette__label">{item.label}</span>
                  <span className="palette__hint">{item.hint}</span>
                </>
              ) : (
                <>
                  <span className="palette__ticker">{item.hit.ticker}</span>
                  <span className="palette__label">{item.hit.name}</span>
                  {/* A ticker that has changed hands is exactly what this
                      corpus exists to keep straight, so the interval is
                      shown rather than the row being hidden. */}
                  {!item.hit.is_current && (
                    <span className="palette__hint">
                      {item.hit.first_day?.slice(0, 4)}–
                      {item.hit.last_day?.slice(0, 4)}
                    </span>
                  )}
                </>
              )}
            </button>
          ))}
        </div>
        <footer className="palette__foot">
          <kbd>↑</kbd>
          <kbd>↓</kbd> to move <kbd>↵</kbd> to open <kbd>esc</kbd> to close
        </footer>
      </div>
    </div>
  );
}
