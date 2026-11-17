/**
 * The application shell: rail, header, and the keyboard layer.
 *
 * Three things live here and nowhere else, so every view inherits them and no
 * view has to remember them: the navigation, the theme and density
 * preferences, and the statement of **which databases are attached**.
 *
 * That last one matters more than it looks. The commonest confusion in this
 * project is reading one database while meaning another — `tradeit status`
 * had to be fixed for it, and `create_app` refuses a default DSN because of
 * it. A screen that does not say what it is reading invites the same mistake,
 * so the header says it at all times.
 */

import { useCallback, useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { getHealth, type Health } from "@/lib/api";
import { CommandPalette } from "./CommandPalette";
import { NAV } from "./nav";
import "./Shell.css";

type Theme = "system" | "light" | "dark";
type Density = "comfortable" | "compact";

function usePreference<T extends string>(
  key: string,
  fallback: T,
  apply: (value: T) => void,
): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      return (localStorage.getItem(key) as T) ?? fallback;
    } catch {
      // Private windows and blocked site data throw here. A preference is a
      // convenience; it must never be able to stop the app rendering.
      return fallback;
    }
  });
  useEffect(() => {
    apply(value);
    try {
      localStorage.setItem(key, value);
    } catch {
      /* ignore — see above */
    }
  }, [key, value, apply]);
  return [value, setValue];
}

export function Shell() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [railOpen, setRailOpen] = useState(true);

  const [theme, setTheme] = usePreference<Theme>(
    "tradeit.theme",
    "system",
    useCallback((value: Theme) => {
      const root = document.documentElement;
      if (value === "system") root.removeAttribute("data-theme");
      else root.setAttribute("data-theme", value);
    }, []),
  );

  const [density, setDensity] = usePreference<Density>(
    "tradeit.density",
    "comfortable",
    useCallback((value: Density) => {
      document.documentElement.setAttribute("data-density", value);
    }, []),
  );

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealthError(true));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const recordName = health?.record?.replace(/^sqlite:\/+/, "") ?? "";

  return (
    <div className={`shell${railOpen ? "" : " shell--narrow"}`}>
      <aside className="rail">
        <div className="rail__brand">
          <button
            type="button"
            className="rail__toggle"
            onClick={() => setRailOpen((open) => !open)}
            aria-label={railOpen ? "Collapse navigation" : "Expand navigation"}
          >
            <span className="rail__mark" aria-hidden="true" />
          </button>
          <span className="rail__name">
            Trade<em>It</em>
          </span>
        </div>

        <nav className="rail__nav">
          {NAV.map((group) => (
            <div className="rail__group" key={group.label}>
              <div className="rail__grouplabel">{group.label}</div>
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    `rail__link${isActive ? " is-on" : ""}`
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="rail__foot">
          <button
            type="button"
            className="rail__pref"
            onClick={() =>
              setTheme(
                theme === "system" ? "light" : theme === "light" ? "dark" : "system",
              )
            }
          >
            Theme<span>{theme}</span>
          </button>
          <button
            type="button"
            className="rail__pref"
            onClick={() =>
              setDensity(density === "comfortable" ? "compact" : "comfortable")
            }
          >
            Density<span>{density}</span>
          </button>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button
            type="button"
            className="topbar__search"
            onClick={() => setPaletteOpen(true)}
          >
            <span>Search securities and views</span>
            <kbd>⌘K</kbd>
          </button>

          {/* Which databases are attached, always visible. The commonest
              confusion in this project is reading one while meaning another. */}
          <div className="topbar__sources">
            {healthError ? (
              <span className="topbar__source is-bad">record unreachable</span>
            ) : (
              <>
                <span className="topbar__source" title={recordName}>
                  record <b>{recordName.split("/").pop() ?? "—"}</b>
                </span>
                <span
                  className={
                    health?.corpus_attached
                      ? "topbar__source"
                      : "topbar__source is-off"
                  }
                >
                  corpus <b>{health?.corpus_attached ? "attached" : "none"}</b>
                </span>
              </>
            )}
          </div>
        </header>

        <main className="content">
          <Outlet context={health} />
        </main>
      </div>

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
