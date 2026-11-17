"""The screen: one self-contained page over the read side's own routes.

Phase 10 asks for a *"dashboard / research interface"*, and until 2026-11-16
the phase had a JSON service and nothing that could open it.

**Rewritten 2026-11-17, because the first version was a diagnostic.** It asked
the reader to type a numeric ``security_id`` --- the screen demanding the
answer it was supposed to give --- printed ``4730362129360`` where a person
reads ``$4.73T``, dumped a JSON components blob into a table cell, and stacked
four equal-weight sections so nothing was more important than anything else.
Every number on it was correct and it looked like a database browser, because
that is what it was.

The rebuild follows three rules.

**One question at a time.** One view at a time, reached from a search box that
takes a ticker or a company name. The corpus holds 6,876 current tickers;
none of them is a number anybody knows.

**The headline is one number, shown as a shape.** A percentile is a figure a
reader has to trust; the series behind it is something they can look at.
*"Cheapest it has ever been"* and *"cheapest since a spike three years ago"*
are the same percentile and different facts, and only the chart distinguishes
them.

**The reasoning stays, folded.** The roadmap's condition is that valuation is
*"never shipped without the reasoning that produced it"*. A disclosure is not
hiding it --- one click, always present, never truncated --- and putting the
arithmetic in front of the number is what made the first version unreadable.

What the page may still not do, unchanged and enforced here rather than left
to taste: no aggregate across horizons, no control that can trade, ``missing``
rendered above results rather than below them, and no security labelled with a
ticker the record does not hold.

**No build step, no package manager, no CDN**, for the reasons the first
version gave: the page is served from the same process as the data, so it
works with no network at all, it cannot drift from the routes it reads, and it
adds nothing to the dependency surface of a repository whose research path is
deliberately installable without even a web framework. The one chart is inline
SVG for the same reason.
"""

from __future__ import annotations

__all__ = ["PAGE"]


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TradeIt</title>
<style>
  :root {
    --bg: #fbfbfa; --surface: #ffffff; --raise: #f6f6f4;
    --ink: #16181c; --mid: #5c6069; --soft: #8b8f98;
    --line: #e7e7e3; --hair: #f0f0ec;
    --accent: #11705c; --accent-soft: #e3f0ec;
    --warn: #8a5114; --warn-soft: #fdf3e8;
    --bad: #9b2c36; --bad-soft: #fbeff0;
    --shadow: 0 1px 2px rgba(20,20,20,.05), 0 8px 24px rgba(20,20,20,.05);
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #0d0e11; --surface: #15171b; --raise: #1b1e23;
      --ink: #eceef1; --mid: #a2a7b0; --soft: #767b85;
      --line: #262a30; --hair: #1e2127;
      --accent: #5fc9a8; --accent-soft: #11261f;
      --warn: #e0a86b; --warn-soft: #251c12;
      --bad: #e8808b; --bad-soft: #241416;
      --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
    }
  }
  :root[data-theme="dark"] {
    --bg: #0d0e11; --surface: #15171b; --raise: #1b1e23;
    --ink: #eceef1; --mid: #a2a7b0; --soft: #767b85;
    --line: #262a30; --hair: #1e2127;
    --accent: #5fc9a8; --accent-soft: #11261f;
    --warn: #e0a86b; --warn-soft: #251c12;
    --bad: #e8808b; --bad-soft: #241416;
    --shadow: 0 1px 2px rgba(0,0,0,.4), 0 8px 24px rgba(0,0,0,.3);
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    background: var(--bg); color: var(--ink);
    font: 15px/1.5 ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI",
          Inter, Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  .num, .mono { font-variant-numeric: tabular-nums;
                font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

  /* ---- shell ---- */
  header { position: sticky; top: 0; z-index: 40; background: var(--bg);
           border-bottom: 1px solid var(--line); }
  .bar { max-width: 1120px; margin: 0 auto; padding: 14px 24px;
         display: flex; gap: 20px; align-items: center; }
  .brand { font-weight: 640; letter-spacing: -0.02em; font-size: 16px;
           white-space: nowrap; }
  .brand span { color: var(--accent); }
  .searchwrap { position: relative; flex: 1; max-width: 460px; }
  #search { width: 100%; font: inherit; font-size: 14px; padding: 9px 13px;
            border: 1px solid var(--line); border-radius: 9px;
            background: var(--surface); color: var(--ink); }
  #search:focus { outline: none; border-color: var(--accent);
                  box-shadow: 0 0 0 3px var(--accent-soft); }
  #results { position: absolute; top: calc(100% + 6px); left: 0; right: 0;
             z-index: 50; background: var(--surface); border: 1px solid var(--line);
             border-radius: 10px; box-shadow: var(--shadow); overflow: hidden;
             max-height: 340px; overflow-y: auto; }
  #results:empty { display: none; }
  .hit { padding: 9px 13px; cursor: pointer; display: flex; gap: 10px;
         align-items: baseline; border-bottom: 1px solid var(--hair); }
  .hit:last-child { border-bottom: none; }
  .hit:hover, .hit.on { background: var(--raise); }
  .hit b { font-size: 13.5px; letter-spacing: 0.01em; }
  .hit span { color: var(--mid); font-size: 12.5px; overflow: hidden;
              text-overflow: ellipsis; white-space: nowrap; }
  .hit em { font-style: normal; color: var(--soft); font-size: 11px;
            margin-left: auto; white-space: nowrap; }
  .record { margin-left: auto; color: var(--soft); font-size: 11.5px;
            text-align: right; max-width: 230px; overflow-wrap: anywhere; }
  nav { max-width: 1120px; margin: 0 auto; padding: 0 24px; display: flex; gap: 2px; }
  nav button { font: inherit; font-size: 13.5px; background: none; border: none;
               color: var(--mid); padding: 9px 13px 11px; cursor: pointer;
               border-bottom: 2px solid transparent; margin-bottom: -1px; }
  nav button:hover { color: var(--ink); }
  nav button.on { color: var(--ink); border-bottom-color: var(--accent);
                  font-weight: 560; }

  main { max-width: 1120px; margin: 0 auto; padding: 28px 24px 100px; }
  .view { display: none; }
  .view.on { display: block; }

  /* ---- company head ---- */
  .head { display: flex; align-items: flex-start; gap: 20px;
          flex-wrap: wrap; margin-bottom: 26px; }
  .tick { font-size: 30px; font-weight: 660; letter-spacing: -0.025em; line-height: 1.1; }
  .co { color: var(--mid); font-size: 14px; margin-top: 2px; }
  .px { margin-left: auto; text-align: right; }
  .px .v { font-size: 24px; font-weight: 600; letter-spacing: -0.02em; }
  .px .d { color: var(--soft); font-size: 12px; }

  /* ---- hero ---- */
  .hero { background: var(--surface); border: 1px solid var(--line);
          border-radius: 14px; padding: 24px; margin-bottom: 18px;
          display: grid; grid-template-columns: minmax(200px, 300px) 1fr;
          gap: 28px; align-items: center; }
  .hero .big { font-size: 52px; font-weight: 660; letter-spacing: -0.035em;
               line-height: 1; color: var(--accent); }
  .hero .cap { font-size: 11.5px; text-transform: uppercase; color: var(--soft);
               letter-spacing: 0.07em; font-weight: 600; margin-bottom: 8px; }
  .hero .ctx { color: var(--mid); font-size: 13.5px; margin-top: 10px; }
  .chart { width: 100%; }
  .chart svg { display: block; width: 100%; height: 108px; overflow: visible; }
  .axis { fill: var(--soft); font-size: 10px; font-family: ui-monospace, Menlo, monospace; }

  /* ---- stats ---- */
  .stats { display: grid; gap: 12px; margin-bottom: 18px;
           grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); }
  .stat { background: var(--surface); border: 1px solid var(--line);
          border-radius: 12px; padding: 15px 17px; }
  .stat .cap { font-size: 11px; text-transform: uppercase; color: var(--soft);
               letter-spacing: 0.06em; font-weight: 600; }
  .stat .v { font-size: 25px; font-weight: 620; letter-spacing: -0.025em;
             margin-top: 5px; line-height: 1.15; }
  .stat .sub { color: var(--mid); font-size: 12.5px; margin-top: 4px; }
  .stat .sub.down { color: var(--warn); }

  /* ---- notices ---- */
  .notice { border-radius: 11px; padding: 13px 16px; margin-bottom: 12px;
            font-size: 13.5px; border: 1px solid; }
  .notice b { display: block; margin-bottom: 3px; font-weight: 620; }
  .notice.warn { background: var(--warn-soft); color: var(--warn);
                 border-color: color-mix(in srgb, var(--warn) 30%, transparent); }
  .notice.bad { background: var(--bad-soft); color: var(--bad);
                border-color: color-mix(in srgb, var(--bad) 30%, transparent); }
  .notice.flat { background: var(--raise); color: var(--mid); border-color: var(--line); }

  /* ---- disclosure ---- */
  details { background: var(--surface); border: 1px solid var(--line);
            border-radius: 12px; margin-bottom: 12px; }
  summary { padding: 13px 17px; cursor: pointer; font-size: 13.5px;
            font-weight: 560; list-style: none; display: flex; gap: 8px;
            align-items: center; color: var(--mid); }
  summary::-webkit-details-marker { display: none; }
  summary::before { content: "\\203a"; display: inline-block; font-size: 17px;
                    transition: transform .15s; color: var(--soft); }
  details[open] summary::before { transform: rotate(90deg); }
  summary:hover { color: var(--ink); }
  .inner { padding: 0 17px 15px; }
  .inner ul { margin: 0; padding-left: 17px; }
  .inner li { margin-bottom: 7px; font-size: 12.5px; color: var(--mid);
              overflow-wrap: anywhere; line-height: 1.5; }

  /* ---- tables ---- */
  .scroller { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  th { text-align: left; padding: 0 14px 8px 0; color: var(--soft);
       font-size: 11px; text-transform: uppercase; letter-spacing: 0.055em;
       font-weight: 600; white-space: nowrap; }
  td { padding: 9px 14px 9px 0; border-top: 1px solid var(--hair); vertical-align: top; }
  td.r, th.r { text-align: right; }
  .card { background: var(--surface); border: 1px solid var(--line);
          border-radius: 12px; padding: 18px 20px; margin-bottom: 16px; }
  .card > h3 { margin: 0 0 14px; font-size: 14px; font-weight: 620;
               letter-spacing: -0.01em; }
  .muted { color: var(--soft); font-size: 13px; }
  .foot { color: var(--soft); font-size: 12px; margin-top: 18px; line-height: 1.55; }
  .pill { display: inline-block; border: 1px solid var(--line); color: var(--mid);
          border-radius: 999px; padding: 1px 8px; font-size: 11px; vertical-align: middle; }
  .horizons { display: grid; gap: 12px;
              grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); }
  .hz { background: var(--surface); border: 1px solid var(--line);
        border-radius: 12px; padding: 15px 17px; }
  .hz h4 { margin: 0 0 9px; font-size: 11.5px; text-transform: uppercase;
           letter-spacing: 0.06em; color: var(--accent); font-weight: 650; }
  .hz.off h4 { color: var(--soft); }
  .hz p { margin: 0; color: var(--mid); font-size: 12.5px; }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
         margin-bottom: 14px; }
  select { font: inherit; font-size: 13.5px; padding: 7px 10px;
           border: 1px solid var(--line); border-radius: 8px;
           background: var(--surface); color: var(--ink); }
  .empty { color: var(--soft); font-size: 13.5px; padding: 28px 0; text-align: center; }

  @media (max-width: 760px) {
    .bar { flex-wrap: wrap; padding: 12px 16px; }
    .searchwrap { max-width: none; order: 3; flex-basis: 100%; }
    .record { max-width: none; }
    nav { padding: 0 16px; overflow-x: auto; }
    main { padding: 20px 16px 80px; }
    .hero { grid-template-columns: 1fr; gap: 18px; padding: 20px; }
    .hero .big { font-size: 42px; }
    .tick { font-size: 25px; }
    .px { margin-left: 0; text-align: left; }
  }
</style>
</head>
<body>
<header>
  <div class="bar">
    <div class="brand">Trade<span>It</span></div>
    <div class="searchwrap">
      <input id="search" type="search" autocomplete="off" spellcheck="false"
             placeholder="Search a ticker or company &mdash; AAPL, Tesla, Lehman">
      <div id="results"></div>
    </div>
    <div class="record" id="record">&nbsp;</div>
  </div>
  <nav>
    <button data-view="valuation" class="on">Valuation</button>
    <button data-view="horizons">Horizons</button>
    <button data-view="decisions">Decisions</button>
    <button data-view="runs">Strategy runs</button>
  </nav>
</header>

<main>
  <section class="view on" id="v-valuation">
    <div id="valuation"><div class="empty">Search for a company to begin.</div></div>
  </section>
  <section class="view" id="v-horizons">
    <div class="row"><label for="inst-pick" class="muted">Security</label>
      <select id="inst-pick"></select></div>
    <div id="panel"></div>
  </section>
  <section class="view" id="v-decisions">
    <div class="row"><label for="session-pick" class="muted">Session</label>
      <select id="session-pick"></select></div>
    <div id="session"></div>
  </section>
  <section class="view" id="v-runs"><div id="runs"></div></section>
</main>

<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

async function get(path) {
  const r = await fetch(path);
  if (!r.ok) { const e = new Error(path + " -> " + r.status); e.status = r.status; throw e; }
  return r.json();
}

// ---- formatting -----------------------------------------------------------
// A dashboard's job is to be read, and 4730362129360 is not read, it is
// decoded. Every figure below is shown the way a person would say it.
const nil = "&mdash;";
function money(v) {
  if (v === null || v === undefined) return nil;
  const n = Number(v), a = Math.abs(n), s = n < 0 ? "-$" : "$";
  if (a >= 1e12) return s + (a / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return s + (a / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return s + (a / 1e6).toFixed(1) + "M";
  if (a >= 1e3) return s + (a / 1e3).toFixed(1) + "K";
  return s + a.toFixed(2);
}
function pct(v, d = 2) {
  return (v === null || v === undefined) ? nil : (Number(v) * 100).toFixed(d) + "%";
}
function mult(v) {
  return (v === null || v === undefined) ? nil : Number(v).toFixed(1) + "\\u00d7";
}
// Scores are small and must never be rounded into nothing: the first version
// of this page rendered 77% of 1,956 real scores as "0.00" with two decimals,
// when the values run from 1.6e-06 to 1.0. A reader would have concluded the
// scorer produced nothing, and the page would have been the evidence for it.
function score(v) {
  if (v === null || v === undefined) return nil;
  const n = Number(v);
  if (!Number.isFinite(n)) return esc(String(v));
  if (n === 0) return "0";
  return Math.abs(n) < 0.0005 ? n.toExponential(2) : n.toFixed(4);
}
const niceDate = (iso) => {
  const d = new Date(String(iso) + "T00:00:00Z");
  return isNaN(d) ? esc(iso) : d.toLocaleDateString(undefined,
    { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
};
const title = (s) => String(s).replace(/_/g, " ").replace(/\\b\\w/g, (c) => c.toUpperCase());

// ---- the chart ------------------------------------------------------------
// Inline SVG, no library. It exists because a percentile is a number a reader
// has to trust and a shape is something they can check: "cheapest ever" and
// "cheapest since one spike" are the same percentile and different facts.
function sparkline(series, current) {
  if (!series || series.length < 2) return '<div class="chart"></div>';
  const pts = series.map((p) => Number(p.value));
  const all = pts.concat([Number(current)]);
  const lo = Math.min(...all), hi = Math.max(...all);
  const span = (hi - lo) || Math.abs(hi) || 1;
  const W = 520, H = 92, PAD = 14;
  const x = (i) => PAD + (i / (series.length - 1)) * (W - PAD * 2);
  const y = (v) => H - PAD - ((v - lo) / span) * (H - PAD * 2);
  const line = series.map((p, i) =>
    (i ? "L" : "M") + x(i).toFixed(1) + " " + y(pts[i]).toFixed(1)).join(" ");
  const area = line + " L " + x(series.length - 1).toFixed(1) + " " + (H - PAD) +
               " L " + x(0).toFixed(1) + " " + (H - PAD) + " Z";
  const cy = y(Number(current)).toFixed(1);
  return '<div class="chart"><svg viewBox="0 0 ' + W + " " + (H + 16) + '"' +
    ' preserveAspectRatio="none" role="img" aria-label="free cash flow yield history">' +
    '<defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1">' +
    '<stop offset="0%" stop-color="var(--accent)" stop-opacity=".22"/>' +
    '<stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>' +
    "</linearGradient></defs>" +
    '<path d="' + area + '" fill="url(#g)"/>' +
    '<path d="' + line + '" fill="none" stroke="var(--accent)" stroke-width="1.6"' +
    ' stroke-linejoin="round" stroke-linecap="round" opacity=".75"/>' +
    '<line x1="' + PAD + '" x2="' + (W - PAD) + '" y1="' + cy + '" y2="' + cy +
    '" stroke="var(--accent)" stroke-width="1" stroke-dasharray="3 3" opacity=".55"/>' +
    '<circle cx="' + x(series.length - 1).toFixed(1) + '" cy="' + cy + '" r="4"' +
    ' fill="var(--accent)" stroke="var(--surface)" stroke-width="2"/>' +
    '<text class="axis" x="' + PAD + '" y="' + (H + 12) + '">' +
    esc(String(series[0].session).slice(0, 4)) + "</text>" +
    '<text class="axis" x="' + (W - PAD) + '" y="' + (H + 12) + '" text-anchor="end">today</text>' +
    '<text class="axis" x="0" y="9">' + pct(hi) + "</text>" +
    '<text class="axis" x="0" y="' + (H + 2) + '">' + pct(lo) + "</text>" +
    "</svg></div>";
}

// ---- search ---------------------------------------------------------------
// The entry point, and the single change that turned this from a diagnostic
// into something usable: nobody knows a security_id, and the first version of
// this page asked the reader for one.
let searchTimer = null, hits = [], cursor = -1;
function renderHits() {
  $("results").innerHTML = hits.map((h, i) => {
    const when = h.is_current ? "" :
      "<em>" + esc(String(h.first_day || "").slice(0, 4)) + "&ndash;" +
      esc(String(h.last_day || "").slice(0, 4)) + "</em>";
    return '<div class="hit' + (i === cursor ? " on" : "") + '" data-id="' +
      h.security_id + '"><b>' + esc(h.ticker) + "</b><span>" + esc(h.name) +
      "</span>" + when + "</div>";
  }).join("");
  for (const el of $("results").querySelectorAll(".hit")) {
    el.onclick = () => openSecurity(el.dataset.id);
  }
}
function clearHits() { hits = []; cursor = -1; $("results").innerHTML = ""; }

$("search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = $("search").value.trim();
  if (q.length < 1) { clearHits(); return; }
  searchTimer = setTimeout(async () => {
    try {
      const d = await get("./securities/search?q=" + encodeURIComponent(q));
      hits = d.results; cursor = hits.length ? 0 : -1; renderHits();
    } catch (e) {
      $("results").innerHTML = '<div class="hit"><span>' +
        (e.status === 501 ? "no corpus attached &mdash; start with --corpus"
                          : esc(e.message)) + "</span></div>";
    }
  }, 160);
});
$("search").addEventListener("keydown", (e) => {
  if (!hits.length) return;
  const move = (step) => {
    cursor = (cursor + step + hits.length) % hits.length;
    renderHits();
    e.preventDefault();
  };
  if (e.key === "ArrowDown") move(1);
  else if (e.key === "ArrowUp") move(-1);
  else if (e.key === "Enter" && cursor >= 0) {
    openSecurity(hits[cursor].security_id);
    e.preventDefault();
  } else if (e.key === "Escape") clearHits();
});
document.addEventListener("click", (e) => {
  if (!e.target.closest(".searchwrap")) clearHits();
});

function openSecurity(id) {
  clearHits();
  $("search").blur();
  show("valuation");
  loadValuation(id);
}

// ---- views ----------------------------------------------------------------
const loaded = {};
function show(name) {
  for (const b of document.querySelectorAll("nav button")) {
    b.classList.toggle("on", b.dataset.view === name);
  }
  for (const v of document.querySelectorAll(".view")) {
    v.classList.toggle("on", v.id === "v-" + name);
  }
  if (!loaded[name] && LOADERS[name]) { loaded[name] = true; LOADERS[name](); }
}
for (const b of document.querySelectorAll("nav button")) b.onclick = () => show(b.dataset.view);

// ---- valuation ------------------------------------------------------------
async function loadValuation(id) {
  $("valuation").innerHTML = '<div class="empty">Loading&hellip;</div>';
  let v;
  try { v = await get("./securities/" + id + "/valuation"); }
  catch (e) {
    $("valuation").innerHTML = '<div class="notice bad"><b>Could not load</b>' +
      (e.status === 501
        ? "Valuation reads the research corpus, a different database from the " +
          "decision record. Start the dashboard with --corpus."
        : esc(e.message)) + "</div>";
    return;
  }
  const who = v.identity;
  let h = '<div class="head"><div><div class="tick">' +
    (who ? esc(who.ticker) : "Security " + v.security_id) + "</div>" +
    '<div class="co">' + (who ? esc(who.name) : "no ticker recorded for this security") +
    // A delisted security shown under a ticker somebody else trades today
    // would be the splice this corpus exists to prevent, arriving as a page
    // heading. The interval is shown instead.
    (who && !who.is_current
      ? ' <span class="pill">last held ' + esc(String(who.last_day || "").slice(0, 4)) + "</span>"
      : "") + "</div></div>";
  if (v.available) {
    h += '<div class="px"><div class="v num">' + money(v.price.close) + "</div>" +
      '<div class="d">' + niceDate(v.price.session) + "</div></div>";
  }
  h += "</div>";

  if (!v.available) {
    $("valuation").innerHTML = h + '<div class="notice flat"><b>No valuation</b>' +
      esc(v.note) + "</div>";
    return;
  }

  if (v.fcf_yield !== null && v.fcf_yield !== undefined) {
    const n = v.fcf_yield_history_size;
    let ctx = "No history to compare against.";
    if (v.fcf_yield_rank !== null && v.fcf_yield_rank !== undefined && n > 0) {
      const cheaper = Math.round(v.fcf_yield_rank * 100);
      const since = v.fcf_yield_history.length
        ? String(v.fcf_yield_history[0].session).slice(0, 4) : "";
      // A higher yield is cheaper, the opposite of a multiple, so the sentence
      // says which way round it is. A reader who takes it for a multiple
      // inverts the conclusion.
      ctx = cheaper === 0
        ? "The lowest yield &mdash; the most expensive &mdash; of " + n +
          " observations since " + esc(since) + "."
        : cheaper === 100
        ? "The highest yield &mdash; the cheapest &mdash; of " + n +
          " observations since " + esc(since) + "."
        : "Cheaper than " + cheaper + "% of its own " + n +
          " observations since " + esc(since) + ".";
    }
    h += '<div class="hero"><div><div class="cap">Free cash flow yield</div>' +
      '<div class="big num">' + pct(v.fcf_yield) + "</div>" +
      '<div class="ctx">' + ctx + "</div></div>" +
      sparkline(v.fcf_yield_history, v.fcf_yield) + "</div>";
  }

  const cards = [];
  if (v.enterprise_value) {
    cards.push(["Enterprise value", money(v.enterprise_value.value),
      money(v.enterprise_value.market_cap) + " equity &middot; " +
      money(v.enterprise_value.debt) + " debt &middot; " +
      money(v.enterprise_value.cash) + " cash", false]);
  }
  if (v.free_cash_flow) {
    const decline = v.quality.find((q) => q.name === "free_cash_flow_declining");
    cards.push(["Free cash flow", money(v.free_cash_flow.value),
      decline ? "Falling over three years"
              : "Fiscal year to " + niceDate(v.free_cash_flow.fiscal_year_end),
      Boolean(decline)]);
  }
  const mid = v.implied_growth[Math.floor(v.implied_growth.length / 2)];
  if (mid) {
    cards.push(["Implied growth", pct(mid.growth, 1),
      "Perpetually, at a " + pct(mid.discount_rate, 0) + " required return", false]);
  }
  for (const m of v.multiples) {
    const r = (m.rank === null || m.rank === undefined) ? "No history"
      : "Dearer than " + Math.round(m.rank * 100) + "% of its own " +
        m.history_size + " observations";
    cards.push([title(m.basis), mult(m.value), r, false]);
  }
  if (cards.length) {
    h += '<div class="stats">' + cards.map((c) =>
      '<div class="stat"><div class="cap">' + c[0] + "</div>" +
      '<div class="v num">' + c[1] + "</div>" +
      '<div class="sub' + (c[3] ? " down" : "") + '">' + c[2] + "</div></div>").join("") +
      "</div>";
  }

  // Warnings, then refusals. A refusal is not an error: "this company loses
  // money" is more useful than a blank, and it is the reason the number is
  // absent.
  for (const q of v.quality) {
    h += '<div class="notice warn"><b>' + esc(title(q.name)) + "</b>" + esc(q.detail) + "</div>";
  }
  for (const u of v.unresolved) {
    h += '<div class="notice flat"><b>' + esc(title(u.basis)) +
      " not computed</b>" + esc(u.reason) + "</div>";
  }
  if (v.enterprise_value && v.enterprise_value.debt_may_be_understated) {
    h += '<div class="notice warn"><b>Debt may be understated</b>' +
      esc(v.enterprise_value.debt_may_be_understated) + "</div>";
  }

  if (v.implied_growth.length > 1) {
    h += '<div class="card"><h3>What this price is paying for</h3>' +
      '<div class="scroller"><table>' +
      '<tr><th>Required return</th><th class="r">Implied growth, perpetually</th></tr>' +
      v.implied_growth.map((g) => "<tr><td>" + pct(g.discount_rate, 0) +
        '</td><td class="r num">' + pct(g.growth, 1) + "</td></tr>").join("") +
      "</table></div></div>";
  }

  // Folded, never removed. The roadmap's condition is that valuation is never
  // shipped without the reasoning that produced it; one click away is not away.
  h += "<details><summary>Show the working &mdash; every figure above, with its arithmetic" +
    "</summary><div class='inner'><ul>" +
    v.explanations.map((l) => "<li>" + esc(l) + "</li>").join("") + "</ul></div></details>";
  h += "<details><summary>What this does not account for (" + v.caveats.length +
    ")</summary><div class='inner'><ul>" +
    v.caveats.map((c) => "<li>" + esc(c) + "</li>").join("") + "</ul></div></details>";
  h += '<p class="foot">' + esc(v.note) + "</p>";
  $("valuation").innerHTML = h;
}

// ---- runs -----------------------------------------------------------------
async function loadRuns() {
  const runs = await get("./runs");
  if (!runs.length) { $("runs").innerHTML = '<div class="empty">No runs recorded.</div>'; return; }
  let h = '<div class="card"><h3>Recorded runs</h3><div class="scroller"><table>' +
    '<tr><th>Run</th><th>Window</th><th class="r">Trades</th><th class="r">CAGR</th>' +
    '<th class="r">Max drawdown</th><th class="r">Sharpe</th><th class="r">Win rate</th>' +
    "<th>Sample</th></tr>";
  const flags = [];
  for (const r of runs) {
    // Sample size beside the ratios, never instead of them: a Sharpe from
    // nineteen trades is a number, not evidence.
    const small = r.statistically_meaningful ? "" : ' <span class="pill">under 30</span>';
    h += "<tr><td>" + esc(r.name) + '<br><span class="muted">' + esc(r.status) + "</span></td>" +
      '<td class="mono">' + esc(r.start) + "<br>" + esc(r.end) + "</td>" +
      '<td class="r num">' + r.trades_recorded + small + "</td>" +
      '<td class="r num">' + pct(r.metrics.cagr) + "</td>" +
      '<td class="r num">' + pct(r.metrics.max_drawdown_pct) + "</td>" +
      '<td class="r num">' +
      (r.metrics.sharpe === null ? nil : Number(r.metrics.sharpe).toFixed(2)) + "</td>" +
      '<td class="r num">' + pct(r.metrics.win_rate) + "</td>" +
      "<td>" + (r.is_out_of_sample ? "Out of sample" : "In sample") + "</td></tr>";
    if (r.trade_count_disagrees) flags.push(esc(r.name) + ": " + esc(r.trade_count_disagrees));
  }
  h += "</table></div></div>";
  // Surfaced, not silently corrected: the displayed count is counted from the
  // trade rows, and the stored column is still wrong on disk for anyone
  // querying the table directly.
  for (const f of flags) {
    h += '<div class="notice warn"><b>Stored trade count disagrees</b>' + f + "</div>";
  }
  $("runs").innerHTML = h;
}

// ---- decisions ------------------------------------------------------------
async function loadSessionList() {
  const d = await get("./sessions");
  if (!d.sessions.length) {
    $("session").innerHTML = '<div class="empty">No sessions recorded.</div>'; return;
  }
  $("session-pick").innerHTML = d.sessions.map((s) =>
    '<option value="' + s.session_date + '">' + niceDate(s.session_date) +
    " &mdash; " + s.scores + " scores</option>").join("");
  $("session-pick").onchange = () => loadSession($("session-pick").value);
  loadSession(d.sessions[0].session_date);
}

async function loadSession(date) {
  $("session").innerHTML = '<div class="empty">Loading&hellip;</div>';
  const d = await get("./sessions/" + date);
  let h = "";
  // `missing` first, above the results. A session with empty lists and no
  // `missing` was quiet; one with empty lists AND `missing` was never
  // recorded, and a reader scanning zero scores would otherwise conclude the
  // system did nothing that day.
  if (d.missing.length) {
    h += '<div class="notice bad"><b>This session cannot be walked end to end</b>' +
      "No record of: " + d.missing.map(esc).join(", ") +
      ". Empty lists below mean nobody wrote the record, not that nothing happened.</div>";
  } else {
    h += '<div class="notice flat"><b>Walkable end to end</b>' +
      "Every link from score to execution is recorded.</div>";
  }
  const sect = (name, rows, cols, cells) => {
    if (!rows.length) {
      return '<div class="card"><h3>' + name + '</h3><p class="muted">None recorded.</p></div>';
    }
    let t = '<div class="card"><h3>' + name + ' <span class="pill">' + rows.length +
      '</span></h3><div class="scroller"><table><tr>' +
      cols.map((c) => "<th>" + c + "</th>").join("") + "</tr>";
    for (const r of rows.slice(0, 50)) t += "<tr>" + cells(r) + "</tr>";
    t += "</table></div>";
    if (rows.length > 50) t += '<p class="muted">Showing the first 50 of ' + rows.length + ".</p>";
    return t + "</div>";
  };
  h += sect("Scores", d.scores, ["Security", "Direction", "Total", "Rank", "Components"],
    (r) => '<td class="mono">#' + r.instrument_id + "</td><td>" + esc(r.direction) +
      '</td><td class="num">' + score(r.total) + '</td><td class="num">' + (r.rank ?? nil) +
      // The breakdown always travels with the total: a total alone is the
      // rationalisation the score's own reconciliation check exists to prevent.
      '</td><td class="mono">' + esc(JSON.stringify(r.components)) + "</td>");
  h += sect("Orders", d.orders, ["Security", "Side", "Quantity", "Status", "Intent"],
    (r) => '<td class="mono">#' + r.instrument_id + "</td><td>" + esc(r.side) +
      '</td><td class="num">' + Number(r.quantity).toLocaleString() + "</td><td>" +
      esc(r.status) + "</td><td>" + esc(r.intent ?? nil) + "</td>");
  h += sect("Executions", d.executions,
    ["Security", "Quantity", "Price", "Commission", "Slippage", "Venue"],
    (r) => '<td class="mono">#' + r.instrument_id + '</td><td class="num">' +
      Number(r.quantity).toLocaleString() + '</td><td class="num">' + money(r.price) +
      '</td><td class="num">' + money(r.commission) + '</td><td class="num">' +
      money(r.slippage) + "</td><td>" + esc(r.venue) + "</td>");
  h += sect("Journal", d.journal, ["Type", "Summary", "Rationale"],
    (r) => "<td>" + esc(r.entry_type) + "</td><td>" + esc(r.summary) + "</td><td>" +
      esc(r.rationale ?? nil) + "</td>");
  $("session").innerHTML = h;
}

// ---- horizons -------------------------------------------------------------
async function loadInstrumentList() {
  const d = await get("./instruments");
  if (!d.instruments.length) {
    $("panel").innerHTML = '<div class="empty">No securities have recorded scores.</div>'; return;
  }
  $("inst-pick").innerHTML = d.instruments.map((i) =>
    '<option value="' + i.instrument_id + '">' +
    (i.ticker ? esc(i.ticker) : "Instrument " + i.instrument_id) +
    " &mdash; " + i.scores + " scores</option>").join("");
  const byId = Object.fromEntries(d.instruments.map((i) => [String(i.instrument_id), i]));
  $("inst-pick").onchange = () => loadPanel($("inst-pick").value, byId[$("inst-pick").value]);
  loadPanel(d.instruments[0].instrument_id, d.instruments[0]);
}

const HORIZON_ORDER = ["day", "swing", "retirement", "unrecorded"];

async function loadPanel(id, meta) {
  $("panel").innerHTML = '<div class="empty">Loading&hellip;</div>';
  const d = await get("./instruments/" + id + "/panel");
  let h = '<div class="head"><div><div class="tick">' +
    (meta && meta.ticker ? esc(meta.ticker) : "Instrument " + id) + "</div>" +
    '<div class="co">' + (meta && meta.ticker_note ? esc(meta.ticker_note) : "&nbsp;") +
    "</div></div></div>";
  // Separate columns, never a combined verdict. Nothing below reads more than
  // one horizon; there is deliberately no cross-horizon arithmetic here.
  const names = HORIZON_ORDER.filter((n) => n in d.horizons || n in d.absent)
    .concat(Object.keys(d.horizons).filter((n) => !HORIZON_ORDER.includes(n)));
  h += '<div class="horizons">';
  for (const name of names) {
    if (name in d.absent) {
      h += '<div class="hz off"><h4>' + esc(name) + "</h4><p>Not scored. " +
        esc(d.absent[name]) + "</p></div>";
      continue;
    }
    const rows = d.horizons[name] || [];
    h += '<div class="hz"><h4>' + esc(name) + "</h4>";
    if (!rows.length) h += "<p>No scores recorded.</p>";
    else {
      h += '<div class="scroller"><table><tr><th>Session</th><th>Dir</th>' +
        '<th class="r">Total</th></tr>';
      for (const r of rows) {
        h += '<tr><td class="mono">' + esc(r.session_date) + "</td><td>" +
          esc(r.direction) + '</td><td class="r num">' + score(r.total) + "</td></tr>";
      }
      h += "</table></div>";
    }
    h += "</div>";
  }
  h += '</div><p class="foot">' + esc(d.note) + "</p>";
  $("panel").innerHTML = h;
}

// ---- boot -----------------------------------------------------------------
const LOADERS = {
  runs: () => guard(loadRuns, "runs"),
  decisions: () => guard(loadSessionList, "session"),
  horizons: () => guard(loadInstrumentList, "panel"),
};
async function guard(fn, where) {
  try { await fn(); }
  catch (e) {
    // Each view fails on its own. A half-loaded screen that says which half
    // failed beats a blank one, and beats one that silently drops a section a
    // reader would take for "nothing recorded".
    $(where).innerHTML = '<div class="notice bad"><b>Could not load</b>' +
      esc(e.message) + "</div>";
  }
}

(async () => {
  try {
    const hh = await get("./health");
    const bits = [];
    if (!hh.has_runs && !hh.has_scores) bits.push("record is empty");
    if (!hh.corpus_attached) bits.push("no corpus \\u2014 valuation disabled");
    $("record").innerHTML = (bits.length ? esc(bits.join(" \\u00b7 ")) + "<br>" : "") +
      '<span class="mono">' + esc(String(hh.record).replace(/^sqlite:\\/+/, "")) + "</span>";
    if (hh.corpus_attached) $("search").focus();
    else {
      $("valuation").innerHTML = '<div class="notice flat"><b>Valuation is disabled</b>' +
        "It reads the research corpus, a different database from the decision record. " +
        "Restart the dashboard with --corpus to enable it.</div>";
    }
  } catch (e) {
    $("record").innerHTML = '<span style="color:var(--bad)">record unreachable</span>';
  }
})();
</script>
</body>
</html>
"""
