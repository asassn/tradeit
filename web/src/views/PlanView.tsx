import type { ReactNode } from "react";
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
import { getPlan, type PlanHorizon } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";
import "./PlanView.css";

/**
 * **What each portfolio should do today, and what it refused.**
 *
 * This is the home screen, and it is the screen this product exists for. The
 * first version of this dashboard opened on a security search box, which made
 * it a stock research browser — an answer to *"is this a great stock"*.
 * CLAUDE.md says the system behaves "like a portfolio manager rather than a
 * stock picker", and that only *"is this a great trade for this portfolio
 * right now"* may authorise a trade. So the portfolio comes first and the
 * research surfaces are reached from it.
 *
 * **The refusals are why the screen is trustworthy rather than merely
 * useful.** A page showing only what a system would buy looks identical
 * whether its limits are working or absent. Each refusal therefore names every
 * limit that bound it and the standing it would have had, which is the only
 * way a cap's cost is legible rather than invisible.
 *
 * **Absence is rendered, not omitted.** Four controls in this codebase cannot
 * fire as currently wired, and a screen listing only what fired could not be
 * told apart from one where everything was armed.
 *
 * **The horizons sit side by side and nothing is computed across them.** A
 * ticker may reach different conclusions at different horizons, and one
 * blended number would destroy exactly the information the three-horizon
 * architecture preserves. There is no total row, by decision.
 */

/** Fixed order, so the columns do not reshuffle as availability changes. */
const ORDER = ["day", "swing", "retirement"];

const title = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** `sector_limit_reached` reads as a sentence; the raw token does not. */
const humanise = (s: string) => s.replace(/_/g, " ");

export function PlanView() {
  const { data, error, loading } = useAsync(getPlan, []);

  if (loading) return <Spinner label="Running the decision path" />;
  if (error) {
    return (
      <>
        <PageHeader
          title="Today's plan"
          subtitle="What each portfolio would do, and what it refused"
        />
        <Unresolved what="The plan" reason={error.message} />
      </>
    );
  }
  if (!data) return <Empty>Nothing to show.</Empty>;

  if (!data.available) {
    return (
      <>
        <PageHeader title="Today's plan" subtitle={data.declaration} />
        <Unresolved
          what="The declared portfolios"
          reason={data.note ?? "The declaration could not be read."}
        />
      </>
    );
  }

  const horizons = [...data.portfolios].sort(
    (a, b) => ORDER.indexOf(a.mandate) - ORDER.indexOf(b.mandate),
  );

  return (
    <>
      <PageHeader
        title="Today's plan"
        subtitle={
          <>
            {horizons.length} horizon{horizons.length === 1 ? "" : "s"}, declared in{" "}
            <code>{data.declaration}</code>. Nothing here places an order.
          </>
        }
        aside={
          data.generated_at ? (
            <div className="plan__stamp">
              {/* A plan with no time on it is a decision of unknown vintage
                  being read as current. */}
              computed {new Date(data.generated_at).toLocaleTimeString()}
            </div>
          ) : undefined
        }
      />

      <div className="plan__columns">
        {horizons.map((horizon) => (
          <Horizon key={horizon.mandate} horizon={horizon} />
        ))}
      </div>
    </>
  );
}

function Horizon({ horizon }: { horizon: PlanHorizon }) {
  if (!horizon.available || !horizon.plan) {
    return (
      <div className="plan__column">
        <h2 className="plan__mandate">{title(horizon.mandate)}</h2>
        {/* It keeps its reason rather than vanishing. A horizon simply absent
            from the page could not be told from one nobody declared. */}
        <Unresolved
          what={`${title(horizon.mandate)} cannot be planned`}
          reason={horizon.note ?? "No reason was given."}
        />
      </div>
    );
  }
  const plan = horizon.plan;
  const unresolved = horizon.loaded?.unresolved ?? [];

  return (
    <div className="plan__column">
      <h2 className="plan__mandate">
        {title(horizon.mandate)}{" "}
        <Pill tone="neutral">{plan.timeframe}</Pill>{" "}
        <span className="plan__session">{plan.session_date}</span>
      </h2>
      {horizon.declared.note && (
        <p className="plan__note">{horizon.declared.note}</p>
      )}

      <StatRow>
        <Stat label="Equity" value={<Figure value={plan.equity} kind="money" />} />
        <Stat
          label="Cash"
          value={<Figure value={plan.cash} kind="money" />}
          context={`${formatFigure(plan.cash / plan.equity, "percent", 1)} of equity`}
        />
        <Stat
          label="Heat"
          // Heat is what is lost if every stop is hit tomorrow. Above its
          // limit it is the reason trades are being refused, so it is the
          // number that explains the rest of the column.
          value={formatFigure(plan.heat, "percent", 2)}
          tone={plan.heat > 0.06 ? "warning" : "neutral"}
          context={
            plan.heat > 0.06
              ? "over the 6% limit — entries are refused"
              : "if every stop were hit tomorrow"
          }
        />
        <Stat
          label="Positions"
          value={<Figure value={plan.position_count} kind="count" />}
          context={`${plan.candidates_considered} candidate${
            plan.candidates_considered === 1 ? "" : "s"
          } considered`}
        />
      </StatRow>

      <Section
        title="Holds"
        count={plan.holdings.length}
        empty="Flat — no open position on this session."
      >
        {plan.holdings.length > 0 && (
          <TableScroller>
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th className="r">Qty</th>
                  <th className="r">Entry</th>
                  <th className="r">Now</th>
                  <th className="r">Stop</th>
                  <th className="r">Value</th>
                  <th className="r">At risk</th>
                  <th className="r">Weight</th>
                </tr>
              </thead>
              <tbody>
                {plan.holdings.map((holding) => (
                  <tr key={holding.instrument_id}>
                    <td className="plan__name">{holding.reference}</td>
                    <td className="r">
                      <Figure value={holding.quantity} kind="quantity" />
                    </td>
                    <td className="r">
                      <Figure value={holding.average_entry_price} kind="money" />
                    </td>
                    <td className="r">
                      <Figure value={holding.last_price} kind="money" />
                    </td>
                    {/* A null stop is rendered as an em dash by `Figure`, never
                        as 0 — a stop at zero is unlimited risk, and an absent
                        stop is a position this system will not hold. */}
                    <td className="r">
                      <Figure value={holding.stop_price} kind="money" />
                    </td>
                    <td className="r">
                      <Figure value={holding.market_value} kind="money" />
                    </td>
                    {/* What is lost if the stop is hit. This is the quantity
                        portfolio heat sums, so it is the column that explains
                        why an entry was refused. */}
                    <td className="r">
                      <Figure value={holding.open_risk} kind="money" />
                    </td>
                    <td className="r">{formatFigure(holding.weight, "percent", 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroller>
        )}
      </Section>

      <Section
        title="Would buy"
        count={plan.would_buy.length}
        empty={
          plan.candidates_considered === 0
            ? "No candidate signalled on this session."
            : "Every candidate was refused or declined."
        }
      >
        {plan.would_buy.length > 0 && (
          <TableScroller>
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th className="r">Qty</th>
                  <th className="r">Entry</th>
                  <th className="r">Stop</th>
                  <th className="r">Risk</th>
                  <th className="r">Weight</th>
                  <th>Sized by</th>
                </tr>
              </thead>
              <tbody>
                {plan.would_buy.map((entry) => (
                  <tr key={entry.instrument_id}>
                    <td className="plan__name">{entry.reference}</td>
                    <td className="r">
                      <Figure value={entry.quantity} kind="quantity" />
                    </td>
                    <td className="r">
                      <Figure value={entry.entry_price} kind="money" />
                    </td>
                    <td className="r">
                      <Figure value={entry.stop_price} kind="money" />
                    </td>
                    <td className="r">
                      <Figure value={entry.risk_amount} kind="money" />
                      <span className="plan__dim">
                        {" "}
                        {formatFigure(entry.risk_fraction, "percent", 2)}
                      </span>
                    </td>
                    <td className="r">{formatFigure(entry.weight, "percent", 1)}</td>
                    {/* "We bought less than we wanted" and "we bought what we
                        wanted" are different facts, and only this says which. */}
                    <td className="plan__dim">{humanise(entry.binding_constraint)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroller>
        )}
      </Section>

      <Section
        title="Would sell"
        count={plan.would_sell.length}
        empty="No stop hit and no exit signalled."
      >
        {plan.would_sell.map((exit) => (
          <div className="plan__row" key={exit.instrument_id}>
            <span className="plan__name">{exit.reference}</span>
            <Pill tone="warning">{humanise(exit.reason)}</Pill>
            <span className="plan__dim">
              {formatFigure(exit.fraction, "percent", 0)} · {exit.detail}
            </span>
          </div>
        ))}
      </Section>

      {/* The section that makes the rest checkable. */}
      <Section
        title="Refused"
        count={plan.refused.length}
        empty="No candidate hit an absolute limit."
      >
        {plan.refused.map((refusal) => (
          <div className="plan__refusal" key={refusal.instrument_id}>
            <div className="plan__row">
              <span className="plan__name">{refusal.reference}</span>
              {/* Every limit that bound it. One would make a second binding
                  limit look slack. */}
              {refusal.reasons.map((reason) => (
                <Pill key={reason} tone="critical">
                  {humanise(reason)}
                </Pill>
              ))}
            </div>
            <div className="plan__dim">
              would have ranked {refusal.standing} · {refusal.detail}
            </div>
          </div>
        ))}
      </Section>

      {plan.declined.length > 0 && (
        <Section title="Declined by risk" count={plan.declined.length} empty="">
          {plan.declined.map((entry) => (
            <div className="plan__row" key={entry.instrument_id}>
              <span className="plan__name">{entry.reference}</span>
              <Pill tone="warning">{humanise(entry.declined_by ?? "risk")}</Pill>
              <span className="plan__dim">{entry.detail}</span>
            </div>
          ))}
        </Section>
      )}

      {unresolved.length > 0 && (
        <Caution title="Not in the universe this planned">
          {unresolved.join(", ")} did not resolve against the corpus, so{" "}
          {unresolved.length === 1 ? "it was" : "they were"} not considered. A
          universe smaller than the one declared is a plan about a portfolio
          nobody described.
        </Caution>
      )}

      {(horizon.loaded?.carried.length ?? 0) > 0 && (
        <Caution title="Marked at an older close">
          {horizon.loaded!.carried
            .map((c) => `${c.reference} (${c.session})`)
            .join(", ")}{" "}
          did not print on this session and {horizon.loaded!.carried.length === 1
            ? "is"
            : "are"}{" "}
          carried at the last price anybody quoted. That cannot trigger a stop
          yesterday did not — but open risk, and therefore heat, is measured
          against it.
        </Caution>
      )}

      {plan.not_armed.length > 0 && (
        <Card title={`Not armed · ${plan.not_armed.length}`}>
          <p className="plan__dim plan__lede">
            Controls that exist in this system and <strong>cannot fire</strong>{" "}
            as currently wired. Listed because a screen showing only what fired
            would look the same as one where everything was armed.
          </p>
          <ul className="plan__absent">
            {plan.not_armed.map((control) => (
              <li key={control.name}>
                <code>{control.name}</code>
                <span className="plan__dim"> — {control.reason}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {/* Beside the numbers, never in a footnote: a figure on a screen is
          acted on, and a document is not. */}
      <Provenance lines={plan.caveats} summary="What this plan is worth" />

      {horizon.loaded && (
        <Provenance
          lines={[
            `${horizon.loaded.sessions_loaded} sessions and ${horizon.loaded.bars_loaded} bars loaded in ${horizon.loaded.seconds}s`,
            `${horizon.declared.universe_size} declared, ${horizon.loaded.sectors_classified} classified by sector`,
            `${horizon.loaded.correlation_pairs} correlation pairs measured, ${horizon.loaded.correlation_over_limit} over the limit — the rule refuses an unmeasured pair rather than assuming independence`,
            `${horizon.declared.fast}/${horizon.declared.slow} moving-average crossover, ${formatFigure(horizon.declared.stop_pct, "percent", 0)} stop`,
            `setup score normalised against a declared [${horizon.declared.score_range.lower}, ${horizon.declared.score_range.upper}] — the raw spread is not a score`,
            `holdings declared by the operator, not read from storage: nothing in this system writes a portfolio row`,
          ]}
          summary="How this plan was made"
        />
      )}
    </div>
  );
}

function Section({
  title: heading,
  count,
  empty,
  children,
}: {
  title: string;
  count: number;
  empty: string;
  children?: ReactNode;
}) {
  return (
    <Card title={`${heading} · ${count}`}>
      {count === 0 ? <p className="plan__dim">{empty}</p> : children}
    </Card>
  );
}
