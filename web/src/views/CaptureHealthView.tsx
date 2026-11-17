import { Caution, Unresolved } from "@/components/Absence";
import { Figure } from "@/components/Figure";
import {
  Card,
  Empty,
  PageHeader,
  Spinner,
  Stat,
  StatRow,
  TableScroller,
} from "@/components/Primitives";
import { ApiError, getForwardHealth } from "@/lib/api";
import { useAsync } from "@/lib/useAsync";

/**
 * The nightly capture agent — the only live-updating data in the system.
 *
 * **Liveness leads, and the totals follow.** A scheduled job does not crash;
 * it stops, and the record simply ceases to grow. A screen showing 5,176
 * observations and a healthy tick while the agent has been dead a week is
 * worse than no screen, so the age of the most recent ask is the headline and
 * everything else is context.
 */
export function CaptureHealthView() {
  const { data, error, loading } = useAsync(getForwardHealth, []);

  if (loading) return <Spinner />;
  if (error) {
    return (
      <Unresolved
        what="Capture health"
        reason={
          error instanceof ApiError && error.status === 501
            ? "The nightly capture writes a different database from the decision record. Start the dashboard with --forward to enable this view."
            : error.message
        }
      />
    );
  }
  if (!data) return <Empty>Nothing to show.</Empty>;
  if (data.asks === 0) {
    return (
      <>
        <PageHeader title="Capture health" />
        <Unresolved what="Capture" reason={data.note ?? "No capture has run."} />
      </>
    );
  }

  const age = data.age_days ?? 0;
  const stale = data.stale === true;

  return (
    <>
      <PageHeader
        title="Capture health"
        subtitle="The unattended nightly agent — the one part of this system that is still moving"
      />

      <Card>
        <div className="hero">
          <div>
            <div className="hero__cap">Most recent ask</div>
            <div
              className="hero__big num"
              style={stale ? { color: "var(--critical)" } : undefined}
            >
              {age < 1
                ? `${Math.round(age * 24)}h ago`
                : `${age.toFixed(1)} days ago`}
            </div>
            <p className="hero__ctx">{data.liveness_note}</p>
          </div>
          <div>
            {/* Status never rests on colour alone: the verdict is a sentence
                as well, because a red number read at a glance by somebody who
                does not know the threshold means nothing. */}
            {stale ? (
              <Caution title="The agent appears to have stopped">
                Nothing has been asked for longer than {data.stale_after_days} days.
                The capture runs on trading sessions, so a Friday ask is three
                days old by Monday and that is normal — beyond that, a session
                was missed.
              </Caution>
            ) : (
              <p className="hero__ctx">
                Within the {data.stale_after_days}-day window. The capture runs
                on trading sessions, so an ask up to three days old over a
                weekend is the agent working, not failing.
              </p>
            )}
          </div>
        </div>
      </Card>

      <StatRow>
        <Stat
          label="Symbol-sessions observed"
          value={<Figure value={data.interpretable_asks} kind="count" />}
          context="The evidence count — asks whose rows_returned describes the session"
        />
        <Stat
          label="Symbols"
          value={<Figure value={data.symbols} kind="count" />}
          context={`across ${data.sessions} sessions`}
        />
        <Stat
          label="Silent symbol-sessions"
          value={<Figure value={data.silent_observations} kind="count" />}
          context="Evidence, not failures — what a delisting is made of"
        />
        <Stat
          label="Earlier asks"
          value={<Figure value={data.earlier_asks} kind="count" />}
          context="Counted the fetch window, not the session — not comparable"
        />
      </StatRow>

      <Card title="Recent sessions" flush>
        {!data.recent_sessions?.length ? (
          <Empty>No sessions recorded.</Empty>
        ) : (
          <TableScroller>
            <table>
              <thead>
                <tr>
                  <th>Session</th>
                  <th className="r">Asks</th>
                  <th className="r">Silent</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_sessions.map((s) => (
                  <tr key={s.session_date}>
                    <td className="mono">{s.session_date}</td>
                    <td className="r">
                      <Figure value={s.asks} kind="count" />
                    </td>
                    <td className="r">
                      <Figure value={s.silent} kind="count" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroller>
        )}
      </Card>

      <p className="foot" style={{ color: "var(--ink-3)", font: "var(--t-caption)" }}>
        {data.silent_note} {data.earlier_note}
      </p>
    </>
  );
}
