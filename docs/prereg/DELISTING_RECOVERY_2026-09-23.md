# Pre-registration — replace the delisting-recovery assumption with evidence

Committed **before any 2013–2019 exit was classified and before any backtest was
re-run under a measured recovery**. Owner-authorised 2026-09-23 as a scoped
proposition: the delisting recovery is a backtesting assumption, and CLAUDE.md
does not permit changing one without this document.

## The assumption, and why it decides things

When a holding stops trading, the engine closes it at a **fraction of its last
price that the caller chooses** — `delisting_recovery`, one number for every
security. Every portfolio result in this project was therefore run twice, at
1.0 and at 0.0, and the two have repeatedly disagreed:

| | recovery 1.0 | recovery 0.0 |
|---|---|---|
| §46, the combination vs random | **+2.54 pp/yr, 4 of 4** | −0.77 pp/yr |
| §46, low volatility alone | 3.07%/yr | **−20.65%/yr** |
| §15, the survivorship gap | ~8 pp | ~57 pp |

**The assumption is doing more work than any signal tested.** §16 already
measured the answer for 1,737 securities of the 2000s population: **78.8%
acquired or extinguished — paid; 4.0% confirmed bankrupt — wiped out; 17.2%
unexplained.** The engine has taken a per-instrument recovery map since
`engine.py` gained `delisting_recovery_by_instrument`; nothing has ever filled
it.

```
PRE-REGISTRATION -- a measured, per-security delisting recovery
written 2026-09-23, BEFORE any 2013-2019 exit was classified

WHAT IS MEASURED
  Every security in §46's four samples whose prices stop inside
  2013-01-02 .. 2019-12-31, classified by tradeit.edgar.exit_cause as
  §16 classified the 2000s population: the registrant's own filings
  from the local full-index, its 8-K header items fetched from EDGAR,
  and the holders of record its Form 15 certifies. The classifier is
  used AS COMMITTED -- no threshold, window or rule is adjusted.

THE MAPPING -- fixed here, from what a holder actually received
  ACQUIRED, EXTINGUISHED            -> recovery 1.0
      A completed transaction, or a Form 15 certifying no public
      holders: the public shares were bought or converted, at about the
      last price. §16's evidence for this class is the strongest it has.
  BANKRUPT                          -> recovery 0.0
      An equity holder in a confirmed bankruptcy receives approximately
      nothing, and §16 verified every bankruptcy header against the
      document text.
  EVERYTHING ELSE                   -> BRACKETED, not guessed
      ACQUISITION_INDICATED, DISTRESS_INDICATED, KEPT_REPORTING,
      DEREGISTERED_UNEXPLAINED, UNRESOLVED. The run is executed twice,
      with the residual at 1.0 and at 0.0, and BOTH readings are
      reported. A single number for the unexplained would be the guess
      this registration exists to remove.
  A security the classifier does not cover is residual, not paid.

WHAT IS THEN RE-RUN
  §46's four samples, four arms, base and stress costs, on 2013-2019 --
  identical in every respect except that recovery comes from the map.
  §48 is NOT re-run here: it failed at recovery 1.0 as well, so no
  recovery assumption can change its verdict, and re-running it would
  invite exactly that hope.

--------------------------------------------------------------------
WHAT THE RESULT DECIDES -- stated before it exists

  1 THE ASSUMPTION. If the measured mix makes the two bracket readings
    agree on §46's verdict, the delisting recovery stops being a free
    choice and becomes a measured input for every future backtest, with
    the residual bracketed. That is the adoption.
  2 IF THE BRACKET STILL DISAGREES, the assumption is NOT settled, the
    per-security map is still adopted -- it is strictly more evidence
    than one number -- and the residual is recorded as the open
    question it is, with its size.
  3 §46's RECORD. Its verdict is amended to what the measured recovery
    shows, in the section itself. §46 FAILED, and it can only stay
    failed or become "failed at 0.0 for a smaller residual" -- because
    §48 refused the same rule out of sample, this cannot reopen the
    combination. Nothing about the strategy is reinstated by this work.

NOT A TRIAL
  No hypothesis about returns is tested. This measures an input and
  re-reads one existing result against it. The ledger stands at 140.

STOP RULE
  If the classifier covers fewer than half of the dead securities in
  these samples, the measurement is too thin to adopt: the single
  number stands, the coverage is recorded, and no substitute mapping is
  invented from price behaviour.
```
