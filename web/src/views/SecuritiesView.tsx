import { useState } from "react";
import { useParams } from "react-router-dom";
import {
  Caution,
  Provenance,
  Unresolved,
} from "@/components/Absence";
import { Figure, formatFigure } from "@/components/Figure";
import {
  Card,
  Empty,
  PageHeader,
  Pill,
  Spinner,
  Stat,
  StatRow,
  TableScroller,
} from "@/components/Primitives";
import { YieldChart } from "@/components/YieldChart";
import { ApiError, getValuation } from "@/lib/api";
import { ProvenanceTab } from "./ProvenanceTab";
import "./SecuritiesView.css";
import { useAsync } from "@/lib/useAsync";

const title = (s: string) =>
  s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

/**
 * One security.
 *
 * **No verdict is rendered, by decision rather than omission** (ADR-0036).
 * A multiple has no absolute meaning without growth and risk; valuation is
 * horizon-dependent, so one label across three horizons would be the
 * universal buy/sell number the architecture exists to prevent; and none of
 * the four fundamental signals measured on this corpus has passed.
 *
 * What the reader gets instead is four facts with their arithmetic attached,
 * and the refusals shown beside the results rather than filtered out — "this
 * company loses money" is more useful than a blank, and it is the *reason*
 * the number is absent.
 */
type Tab = "valuation" | "provenance";

export function SecuritiesView() {
  const { securityId } = useParams();
  const [tab, setTab] = useState<Tab>("valuation");
  const { data, error, loading } = useAsync(
    () =>
      securityId ? getValuation(securityId) : Promise.resolve(null),
    [securityId],
  );

  if (!securityId) {
    return (
      <>
        <PageHeader
          title="Securities"
          subtitle="41,698 securities, 20,124 ticker intervals, prices to 2026"
        />
        <Empty>
          Press <kbd>⌘K</kbd> and type a ticker or a company name.
        </Empty>
      </>
    );
  }

  if (loading) return <Spinner />;
  if (error) {
    return (
      <Unresolved
        what="This security"
        reason={
          error instanceof ApiError && error.status === 501
            ? "Valuation reads the research corpus, which is a different database from the decision record. Start the dashboard with --corpus to enable it."
            : error.message
        }
      />
    );
  }
  if (!data) return <Empty>Nothing to show.</Empty>;

  const who = data.identity;
  const rank = data.fcf_yield_rank;
  const size = data.fcf_yield_history_size ?? 0;
  const history = data.fcf_yield_history ?? [];

  // Higher yield is cheaper — the opposite of a multiple — so the sentence
  // says which way round it is. A reader who takes it for a multiple inverts
  // the conclusion.
  let context = "No history to compare against.";
  if (rank !== null && rank !== undefined && size > 0) {
    const pct = Math.round(rank * 100);
    const since = history[0]?.session.slice(0, 4) ?? "";
    context =
      pct === 0
        ? `The lowest yield — the most expensive — of ${size} observations since ${since}.`
        : pct === 100
          ? `The highest yield — the cheapest — of ${size} observations since ${since}.`
          : `Cheaper than ${pct}% of its own ${size} observations since ${since}.`;
  }

  const decline = data.quality?.find((q) => q.name === "free_cash_flow_declining");
  const mid = data.implied_growth?.[Math.floor((data.implied_growth.length ?? 1) / 2)];

  return (
    <>
      <PageHeader
        title={who ? who.ticker : `Security ${data.security_id}`}
        subtitle={
          <>
            {who ? who.name : "No ticker recorded for this security"}{" "}
            {/* A delisted security shown under a ticker somebody else trades
                today would be the splice this corpus exists to prevent,
                arriving as a page heading. */}
            {who && !who.is_current && (
              <Pill tone="warning">
                last held {who.last_day?.slice(0, 4)}
              </Pill>
            )}
          </>
        }
        aside={
          data.available && data.price ? (
            <>
              <div style={{ font: "600 24px/1.2 var(--font)", letterSpacing: "-0.02em" }}>
                <Figure value={data.price.close} kind="money" />
              </div>
              <div style={{ color: "var(--ink-3)", font: "var(--t-caption)" }}>
                {data.price.session}
              </div>
            </>
          ) : undefined
        }
      />

      <nav className="tabs" role="tablist">
        {(["valuation", "provenance"] as const).map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={tab === name}
            className={`tabs__tab${tab === name ? " is-on" : ""}`}
            onClick={() => setTab(name)}
          >
            {name === "valuation" ? "Valuation" : "Provenance"}
          </button>
        ))}
      </nav>

      {tab === "provenance" && <ProvenanceTab securityId={securityId} />}

      {tab === "valuation" && !data.available && (
        <Unresolved what="Valuation" reason={data.note ?? "No data."} />
      )}

      {tab === "valuation" && data.available && (
        <>
          {data.fcf_yield !== null && data.fcf_yield !== undefined && (
            <Card>
              <div className="hero">
                <div>
                  <div className="hero__cap">Free cash flow yield</div>
                  <div className="hero__big num">
                    {formatFigure(data.fcf_yield, "percent")}
                  </div>
                  <p className="hero__ctx">{context}</p>
                </div>
                <YieldChart series={history} current={data.fcf_yield} />
              </div>
            </Card>
          )}

          <StatRow>
            {data.enterprise_value && (
              <Stat
                label="Enterprise value"
                value={<Figure value={data.enterprise_value.value} kind="money" />}
                context={
                  <>
                    {formatFigure(data.enterprise_value.market_cap, "money")} equity
                    {" · "}
                    {formatFigure(data.enterprise_value.debt, "money")} debt
                    {" · "}
                    {formatFigure(data.enterprise_value.cash, "money")} cash
                  </>
                }
              />
            )}
            {data.free_cash_flow && (
              <Stat
                label="Free cash flow"
                value={<Figure value={data.free_cash_flow.value} kind="money" />}
                tone={decline ? "warning" : "neutral"}
                context={
                  decline
                    ? "Falling over three years"
                    : `Fiscal year to ${data.free_cash_flow.fiscal_year_end}`
                }
              />
            )}
            {mid && (
              <Stat
                label="Implied growth"
                value={formatFigure(mid.growth, "percent", 1)}
                context={`Perpetually, at a ${formatFigure(mid.discount_rate, "percent", 0)} required return`}
              />
            )}
            {(data.multiples ?? []).map((m) => (
              <Stat
                key={m.basis}
                label={title(m.basis)}
                value={<Figure value={m.value} kind="multiple" />}
                context={
                  m.rank === null || m.rank === undefined
                    ? "No history"
                    : `Dearer than ${Math.round(m.rank * 100)}% of its own ${m.history_size} observations`
                }
              />
            ))}
          </StatRow>

          {(data.quality ?? []).map((q) => (
            <Caution key={q.name} title={title(q.name)}>
              {q.detail}
            </Caution>
          ))}

          {data.enterprise_value?.debt_may_be_understated && (
            <Caution title="Debt may be understated">
              {data.enterprise_value.debt_may_be_understated}
            </Caution>
          )}

          {/* Refusals beside the results, never filtered out. */}
          {(data.unresolved ?? []).map((u) => (
            <Unresolved key={u.basis} what={title(u.basis)} reason={u.reason} />
          ))}

          {(data.implied_growth?.length ?? 0) > 1 && (
            <Card title="What this price is paying for" flush>
              <TableScroller>
                <table>
                  <thead>
                    <tr>
                      <th>Required return</th>
                      <th className="r">Implied growth, perpetually</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.implied_growth!.map((g) => (
                      <tr key={g.discount_rate}>
                        <td>{formatFigure(g.discount_rate, "percent", 0)}</td>
                        <td className="r">
                          <Figure value={g.growth} kind="percent" precision={1} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableScroller>
            </Card>
          )}

          <Provenance lines={data.explanations ?? []} />
          <Provenance
            lines={data.caveats ?? []}
            summary="What this does not account for"
          />
        </>
      )}
    </>
  );
}
