"""The screen: one self-contained page over the read side's own routes.

Phase 10 asks for a *"dashboard / research interface"*, and until now the
phase had a JSON service and nothing that could open it. The service was
imported by its own test and by two documents, and by nothing else --- the
same shape the Phase 7 audit found and named, *"built, tested and standing
beside the decision rather than in it"*.

**Why a string of HTML and not a front end.** No build step, no package
manager, no CDN. The page is served from the same process as the data, so it
works with no network at all, it cannot drift from the routes it reads
(they are in the same package), and it adds nothing to the dependency surface
of a repository whose research path is deliberately installable without even a
web framework. A React application here would be a second toolchain to keep
alive for a page that renders five tables.

**What the page may not do, enforced here rather than left to taste.**

*No aggregate across horizons.* The roadmap forbids a global buy/sell number
and the panel renders Day, Swing and Retirement as separate columns. There is
no arithmetic in this file that takes more than one horizon as input, and the
absent ones render their stated reason instead of an empty cell --- an empty
cell reads as *looked and found nothing*, and for Day nothing was ever looked
at and nothing can be.

*No trading controls.* There is no form, no button that POSTs, and no route to
receive one. *"A dashboard that can place an order is a trading system with a
chart attached."*

*`missing` renders before results, not after.* A session with empty lists and
no ``missing`` is a quiet session; a session with empty lists *and* ``missing``
is one nobody recorded. The second is the more dangerous reading and is put at
the top of the panel, in the colour the page reserves for it, because a reader
scanning a list of zero scores will otherwise conclude the system did nothing
that day rather than that the record is silent.

*Nothing is labelled with a ticker the record does not hold.* The API returns
``ticker_note`` where identity is unresolved and the page prints the instrument
id with that note, rather than a blank that would read as a security without a
symbol.
"""

from __future__ import annotations

__all__ = ["PAGE"]


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TradeIt research interface</title>
<style>
  :root {
    --bg: #fbfbfa; --panel: #fff; --ink: #1a1a18; --soft: #6b6b63;
    --line: #e4e4de; --accent: #2f5d50; --warn: #8a4b1f; --warnbg: #fdf4ec;
    --missing: #8c2f39; --missingbg: #fbf0f1;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --bg: #16171a; --panel: #1d1f23; --ink: #e8e8e4; --soft: #9a9a94;
      --line: #2e3137; --accent: #7fbfa8; --warn: #d9a06a; --warnbg: #2a2119;
      --missing: #e0828c; --missingbg: #2b1b1e;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  .wrap { max-width: 1100px; margin: 0 auto; padding: 24px 16px 80px; }
  header { border-bottom: 1px solid var(--line); padding-bottom: 14px; margin-bottom: 22px; }
  h1 { font-size: 19px; margin: 0 0 4px; letter-spacing: -0.01em; }
  h2 { font-size: 15px; margin: 0 0 10px; letter-spacing: -0.01em; }
  /* The record label is a full DSN and the owner reads this on a phone, so it
     wraps mid-path rather than pushing the page sideways. */
  .sub { color: var(--soft); font-size: 13px; overflow-wrap: anywhere; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
  /* Each panel scrolls its own wide table rather than letting the page scroll
     sideways. Measured at 375px: without this the runs table's last columns and
     the components cell sit off-screen with no indication they are there. */
  section { background: var(--panel); border: 1px solid var(--line);
            border-radius: 10px; padding: 16px; margin-bottom: 18px;
            overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  th, td { text-align: left; padding: 6px 10px 6px 0; border-bottom: 1px solid var(--line);
           vertical-align: top; }
  th { color: var(--soft); font-weight: 600; font-size: 12px; text-transform: uppercase;
       letter-spacing: 0.04em; }
  tr:last-child td { border-bottom: none; }
  .num { text-align: right; font-family: ui-monospace, Menlo, monospace; }
  a { color: var(--accent); text-decoration: none; cursor: pointer; }
  a:hover { text-decoration: underline; }
  .flag { background: var(--warnbg); color: var(--warn); border: 1px solid currentColor;
          border-radius: 6px; padding: 8px 10px; font-size: 12.5px; margin: 8px 0; }
  .missing { background: var(--missingbg); color: var(--missing);
             border: 1px solid currentColor; border-radius: 6px; padding: 10px 12px;
             font-size: 13px; margin-bottom: 14px; }
  .ok { color: var(--soft); font-size: 12.5px; margin-bottom: 14px; }
  .note { color: var(--soft); font-size: 12px; margin-top: 10px; font-style: italic; }
  .horizons { display: grid; gap: 12px;
               grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); }
  .horizon { border: 1px solid var(--line); border-radius: 8px; padding: 12px; }
  .horizon h3 { margin: 0 0 8px; font-size: 13px; text-transform: uppercase;
                letter-spacing: 0.05em; color: var(--accent); }
  .horizon.absent h3 { color: var(--soft); }
  .absent-why { color: var(--soft); font-size: 12.5px; }
  select, input { font: inherit; padding: 5px 8px; border: 1px solid var(--line);
                  border-radius: 6px; background: var(--panel); color: var(--ink); }
  .row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
  .pill { display: inline-block; border: 1px solid var(--line); border-radius: 999px;
          padding: 1px 8px; font-size: 11.5px; color: var(--soft); margin-right: 4px; }
  .empty { color: var(--soft); font-size: 13px; }
  .headline { margin: 12px 0 16px; }
  .big { font-size: 30px; font-weight: 650; letter-spacing: -0.02em; color: var(--accent); }
  ul.lines { padding-left: 18px; margin: 6px 0; }
  ul.lines li { margin-bottom: 5px; overflow-wrap: anywhere; }
  h3.sub { font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em;
           margin: 16px 0 6px; font-weight: 600; }
  @media (max-width: 620px) {
    .wrap { padding: 16px; }
    table { font-size: 12.5px; }
    /* The JSON breakdown is the widest thing on the page. Capped so it cannot
       stretch the table past the panel; it still scrolls into view. */
    td.mono { max-width: 46vw; overflow-wrap: anywhere; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>TradeIt research interface</h1>
    <div class="sub" id="whoami">reading&hellip;</div>
  </header>

  <section id="runs-panel">
    <h2>Recorded runs</h2>
    <div id="runs">loading&hellip;</div>
  </section>

  <section id="session-panel">
    <h2>What was decided on one session</h2>
    <div class="row">
      <label for="session-pick" class="sub">Session</label>
      <select id="session-pick"></select>
    </div>
    <div id="session">pick a session</div>
  </section>

  <section id="valuation-panel">
    <h2>What a company costs, and what it produces</h2>
    <div class="row">
      <label for="val-pick" class="sub">Security id</label>
      <input id="val-pick" type="number" min="1" value="1" style="width: 110px">
      <a id="val-go">look up</a>
    </div>
    <div id="valuation">enter a security id</div>
  </section>

  <section id="panel-panel">
    <h2>One security, each horizon separately</h2>
    <div class="row">
      <label for="inst-pick" class="sub">Security</label>
      <select id="inst-pick"></select>
    </div>
    <div id="panel">pick a security</div>
  </section>

  <p class="note">
    Read-only. This interface has no trading authority and computes no score
    across horizons: a security may be attractive at one horizon and
    unattractive at another, which is information rather than a contradiction.
  </p>
</div>

<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const pct = (v) => (v === null || v === undefined) ? "&mdash;" : (v * 100).toFixed(2) + "%";
const num = (v, d = 2) => (v === null || v === undefined) ? "&mdash;" : Number(v).toFixed(d);

// Opportunity scores, which are small and must never be rounded into nothing.
//
// This exists because the first draft of this page used `num` for them and
// **77% of the 1,956 recorded scores rendered as `0.00`** -- the real values
// run from 1.6e-06 to 1.0, so two decimals turned most of a working record
// into a column of zeros. A reader would have concluded the scorer was
// producing nothing, and the page would have been the evidence for it. That
// is the dashboard failure mode worth naming: not a crash, which gets fixed,
// but a confident wrong number, which gets believed and acted on.
//
// So a non-zero score may never display as zero. Below the point where fixed
// notation would swallow it the format switches to exponential rather than
// losing the magnitude, and an exact zero is printed as a bare "0" so it
// cannot be confused with a small number that was rounded.
function score(v) {
  if (v === null || v === undefined) return "&mdash;";
  const n = Number(v);
  if (!Number.isFinite(n)) return esc(String(v));
  if (n === 0) return "0";
  return Math.abs(n) < 0.0005 ? n.toExponential(2) : n.toFixed(4);
}

async function get(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(path + " -> " + r.status);
  return r.json();
}

// A security's label. Never invents one: where the record holds no mapping the
// id is shown with the reason, because a blank would read as "no ticker" when
// the truth is "nobody wrote one down".
function label(row) {
  if (row.ticker) return esc(row.ticker) + ' <span class="pill">#' + row.instrument_id + "</span>";
  return '<span class="mono">instrument ' + row.instrument_id + "</span>";
}

async function loadHealth() {
  try {
    const h = await get("./health");
    const bits = [];
    bits.push("record: " + esc(h.record));
    bits.push(h.has_runs ? "runs recorded" : "no runs");
    bits.push(h.has_scores ? "scores recorded" : "no scores");
    $("whoami").innerHTML = bits.join(" &middot; ") +
      (h.note ? '<div class="flag">' + esc(h.note) + "</div>" : "");
  } catch (e) {
    $("whoami").innerHTML = '<div class="missing">the record is not reachable: ' +
      esc(e.message) + "</div>";
  }
}

async function loadRuns() {
  const runs = await get("./runs");
  if (!runs.length) { $("runs").innerHTML = '<p class="empty">no runs recorded</p>'; return; }
  let html = "<table><tr><th>Run</th><th>Window</th><th class='num'>Trades</th>" +
    "<th class='num'>CAGR</th><th class='num'>Max DD</th><th class='num'>Sharpe</th>" +
    "<th class='num'>Win</th><th>Sample</th></tr>";
  const flags = [];
  for (const r of runs) {
    // Sample size is shown beside the ratios, never instead of them: a Sharpe
    // from nineteen trades is a number, not evidence.
    const meaningful = r.statistically_meaningful ? "" :
      ' <span class="pill">under 30</span>';
    html += "<tr><td>" + esc(r.name) + ' <span class="pill">' + esc(r.status) + "</span></td>" +
      '<td class="mono">' + esc(r.start) + " &rarr; " + esc(r.end) + "</td>" +
      '<td class="num">' + r.trades_recorded + meaningful + "</td>" +
      '<td class="num">' + pct(r.metrics.cagr) + "</td>" +
      '<td class="num">' + pct(r.metrics.max_drawdown_pct) + "</td>" +
      '<td class="num">' + num(r.metrics.sharpe) + "</td>" +
      '<td class="num">' + pct(r.metrics.win_rate) + "</td>" +
      "<td>" + (r.is_out_of_sample ? "out of sample" : "in sample") + "</td></tr>";
    if (r.trade_count_disagrees) flags.push(esc(r.name) + ": " + esc(r.trade_count_disagrees));
  }
  html += "</table>";
  // Surfaced, not silently corrected. The displayed count is the one counted
  // from the trade rows; the disagreement says the stored column is still
  // wrong on disk for anyone querying the table directly.
  for (const f of flags) html += '<div class="flag">' + f + "</div>";
  $("runs").innerHTML = html;
}

async function loadSessionList() {
  const data = await get("./sessions");
  const pick = $("session-pick");
  if (!data.sessions.length) {
    $("session").innerHTML = '<p class="empty">no sessions recorded</p>';
    return;
  }
  pick.innerHTML = data.sessions
    .map((s) => '<option value="' + s.session_date + '">' + s.session_date +
      " (" + s.scores + " scores)</option>").join("");
  pick.onchange = () => loadSession(pick.value);
  loadSession(data.sessions[0].session_date);
}

async function loadSession(date) {
  $("session").innerHTML = "loading&hellip;";
  const d = await get("./sessions/" + date);
  let html = "";
  // `missing` first, always, and before any list of results. An empty list
  // under a silent record reads as "nothing happened" otherwise.
  if (d.missing.length) {
    html += '<div class="missing"><strong>This session cannot be walked end to end.</strong>' +
      "<br>No record of: " + d.missing.map(esc).join(", ") +
      "<br>Empty lists below mean nobody wrote the record, not that nothing happened.</div>";
  } else {
    html += '<div class="ok">Walkable end to end: every link from score ' +
      "to execution is recorded.</div>";
  }
  const section = (title, rows, cols, cells) => {
    if (!rows.length) return "<h3 class='sub'>" + title + "</h3><p class='empty'>none recorded</p>";
    let t = "<h3 class='sub'>" + title + " (" + rows.length + ")</h3><table><tr>" +
      cols.map((c) => "<th>" + c + "</th>").join("") + "</tr>";
    for (const r of rows.slice(0, 50)) t += "<tr>" + cells(r) + "</tr>";
    t += "</table>";
    if (rows.length > 50) t += '<p class="note">showing the first 50 of ' + rows.length + "</p>";
    return t;
  };
  html += section("Scores", d.scores, ["Security", "Direction", "Total", "Rank", "Components"],
    (r) => '<td class="mono">#' + r.instrument_id + "</td><td>" + esc(r.direction) +
      '</td><td class="num">' + score(r.total) + '</td><td class="num">' + (r.rank ?? "&mdash;") +
      // The breakdown always travels with the total. A total alone is the
      // rationalisation the score's own reconciliation check exists to prevent.
      '</td><td class="mono">' + esc(JSON.stringify(r.components)) + "</td>");
  html += section("Orders", d.orders, ["Security", "Side", "Qty", "Status", "Intent"],
    (r) => '<td class="mono">#' + r.instrument_id + "</td><td>" + esc(r.side) +
      '</td><td class="num">' + esc(r.quantity) + "</td><td>" + esc(r.status) +
      "</td><td>" + esc(r.intent ?? "&mdash;") + "</td>");
  html += section("Executions", d.executions,
    ["Security", "Qty", "Price", "Commission", "Slippage", "Venue"],
    (r) => '<td class="mono">#' + r.instrument_id + '</td><td class="num">' + esc(r.quantity) +
      '</td><td class="num">' + esc(r.price) + '</td><td class="num">' + esc(r.commission) +
      '</td><td class="num">' + esc(r.slippage) + "</td><td>" + esc(r.venue) + "</td>");
  html += section("Journal", d.journal, ["Type", "Summary", "Rationale"],
    (r) => "<td>" + esc(r.entry_type) + "</td><td>" + esc(r.summary) +
      "</td><td>" + esc(r.rationale ?? "&mdash;") + "</td>");
  $("session").innerHTML = html;
}

async function loadInstrumentList() {
  const data = await get("./instruments");
  const pick = $("inst-pick");
  if (!data.instruments.length) {
    $("panel").innerHTML = '<p class="empty">no securities have recorded scores</p>';
    return;
  }
  pick.innerHTML = data.instruments.map((i) =>
    '<option value="' + i.instrument_id + '">' +
    (i.ticker ? i.ticker : "instrument " + i.instrument_id) +
    " &mdash; " + i.scores + " scores</option>").join("");
  const byId = Object.fromEntries(data.instruments.map((i) => [String(i.instrument_id), i]));
  pick.onchange = () => loadPanel(pick.value, byId[pick.value]);
  const first = data.instruments[0];
  loadPanel(first.instrument_id, first);
}

const HORIZON_ORDER = ["day", "swing", "retirement", "unrecorded"];

async function loadPanel(id, meta) {
  $("panel").innerHTML = "loading&hellip;";
  const d = await get("./instruments/" + id + "/panel");
  let html = "<p>" + label({ ticker: meta && meta.ticker, instrument_id: id }) + "</p>";
  if (meta && meta.ticker_note) {
    html += '<div class="flag">' + esc(meta.ticker_note) + "</div>";
  }
  // Separate columns, never a combined verdict. Nothing below reads more than
  // one horizon; there is deliberately no cross-horizon arithmetic here.
  const names = HORIZON_ORDER.filter((n) => n in d.horizons || n in d.absent)
    .concat(Object.keys(d.horizons).filter((n) => !HORIZON_ORDER.includes(n)));
  html += '<div class="horizons">';
  for (const name of names) {
    if (name in d.absent) {
      html += '<div class="horizon absent"><h3>' + esc(name) + "</h3>" +
        '<div class="absent-why">Not scored. ' + esc(d.absent[name]) + "</div></div>";
      continue;
    }
    const rows = d.horizons[name] || [];
    html += '<div class="horizon"><h3>' + esc(name) + "</h3>";
    if (!rows.length) {
      html += '<div class="absent-why">no scores recorded</div>';
    } else {
      html += "<table><tr><th>Session</th><th>Dir</th><th class='num'>Total</th></tr>";
      for (const r of rows) {
        html += '<tr><td class="mono">' + esc(r.session_date) + "</td><td>" +
          esc(r.direction) + '</td><td class="num">' + score(r.total) + "</td></tr>";
      }
      html += "</table>";
    }
    html += "</div>";
  }
  html += "</div>";
  html += '<p class="note">' + esc(d.note) + "</p>";
  $("panel").innerHTML = html;
}

// Valuation. Reads the corpus, not the record, and renders no verdict --
// ADR-0036. The two rank directions are opposite and both are labelled on
// screen, because a yield inverts a multiple and reading one for the other
// reverses the conclusion.
async function loadValuation(id) {
  $("valuation").innerHTML = "loading&hellip;";
  let v;
  try {
    v = await get("./securities/" + id + "/valuation");
  } catch (e) {
    $("valuation").innerHTML = '<div class="flag">' +
      (String(e.message).includes("501")
        ? "no corpus attached &mdash; start the dashboard with --corpus to enable valuation"
        : esc(e.message)) + "</div>";
    return;
  }
  if (!v.available) {
    $("valuation").innerHTML = '<div class="flag">' + esc(v.note) + "</div>";
    return;
  }
  let html = '<p class="mono">security ' + v.security_id + " &middot; close " +
    num(v.price.close, 4) + " on " + esc(v.price.session) + "</p>";

  if (v.fcf_yield !== null && v.fcf_yield !== undefined) {
    const rank = (v.fcf_yield_rank === null || v.fcf_yield_rank === undefined)
      ? "no history to compare"
      : pct(v.fcf_yield_rank) + " of its own " + v.fcf_yield_history_size +
        " past observations were at or below &mdash; <strong>higher is cheaper</strong>";
    html += '<div class="headline"><span class="big">' + pct(v.fcf_yield) +
      '</span><span class="sub"> free cash flow yield on enterprise value</span>' +
      '<div class="sub">' + rank + "</div></div>";
  }

  if (v.implied_growth.length) {
    html += "<h3 class='sub'>What this price is paying for</h3><table>" +
      "<tr><th>Required return</th><th class='num'>Implied growth, perpetually</th></tr>";
    for (const g of v.implied_growth) {
      html += "<tr><td>" + pct(g.discount_rate) + '</td><td class="num">' +
        pct(g.growth) + "</td></tr>";
    }
    html += "</table>";
  }

  for (const q of v.quality) {
    html += '<div class="flag"><strong>' + esc(q.name.replace(/_/g, " ")) +
      "</strong><br>" + esc(q.detail) + "</div>";
  }

  if (v.multiples.length) {
    html += "<h3 class='sub'>Multiples (secondary)</h3><table>" +
      "<tr><th>Basis</th><th class='num'>Value</th><th>Vs its own history</th></tr>";
    for (const m of v.multiples) {
      const r = (m.rank === null || m.rank === undefined)
        ? "&mdash;"
        : pct(m.rank) + " at or below of " + m.history_size + " (lower is cheaper)";
      html += "<tr><td>" + esc(m.basis.replace(/_/g, " ")) + '</td><td class="num">' +
        num(m.value) + "</td><td>" + r + "</td></tr>";
    }
    html += "</table>";
  }

  // Refusals are shown, never filtered. "This company loses money" is more
  // useful than a blank cell, and it is the reason the number is absent.
  for (const u of v.unresolved) {
    html += '<div class="flag"><strong>' + esc(u.basis.replace(/_/g, " ")) +
      " not computed</strong><br>" + esc(u.reason) + "</div>";
  }

  html += "<h3 class='sub'>Check the arithmetic</h3><ul class='mono lines'>";
  for (const line of v.explanations) html += "<li>" + esc(line) + "</li>";
  html += "</ul>";
  for (const c of v.caveats) html += '<p class="note">' + esc(c) + "</p>";
  html += '<p class="note">' + esc(v.note) + "</p>";
  $("valuation").innerHTML = html;
}

(async () => {
  await loadHealth();
  const valGo = () => loadValuation($("val-pick").value);
  $("val-go").onclick = valGo;
  $("val-pick").onkeydown = (e) => { if (e.key === "Enter") valGo(); };
  for (const [fn, where] of [[loadRuns, "runs"], [loadSessionList, "session"],
                             [loadInstrumentList, "panel"], [valGo, "valuation"]]) {
    // Each panel fails on its own. One unreachable route must not blank the
    // page: a half-loaded screen that says which half failed is more useful
    // than a blank one, and far more useful than a screen that silently drops
    // a section a reader would take for "nothing recorded".
    try { await fn(); }
    catch (e) { $(where).innerHTML = '<div class="missing">could not load: ' +
      esc(e.message) + "</div>"; }
  }
})();
</script>
</body>
</html>
"""
