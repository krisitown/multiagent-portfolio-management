# Report consistency: anonymized worked examples

These fictional examples teach checks, not company facts or required investment decisions. Names, dates, quantities, and prices are invented. Apply a pattern only when the submitted packet supports it. State the conflicting passages and the smallest necessary repair; do not invent missing evidence or demand a particular strategy.

## One implementable action per position and time

Faulty: a position ledger says “retain the March puts,” but today's funded orders sell those same puts in full. A commodity sleeve is marked “do not add,” while today's allocation spends €600 buying it. These are contradictions unless explicitly separated by scenario or time. Reconcile the ledger, prose, orders, and cash bridge together; do not choose the author's intended action for them.

Correct: “Hold today; sell only if condition X occurs” with no sale funded today. “No addition today; a conditional €600 purchase next quarter, excluded from today's cash bridge.” Do not flag these as contradictory just because both hold and sell/buy appear.

## Preserve explicit owner constraints and distinguish proposals

Faulty: the owner explicitly requests keeping the Alder and Birch overlays, but a generic “close all overlays” order includes both and calls this the owner's instruction. Identify the conflict and attribution error. The author must preserve the constraint or explicitly present an alternative requiring the owner's reconsideration.

Correct: “Your instruction is to retain Alder. Here is a separately labelled alternative if you choose to relax that constraint; it is not in the implementation orders.” Disagreement with the owner is not itself a factual error.

## Rows, contracts, and contingent shares are different

Faulty: four lines with quantities 3, 1, 2, and 1 are described as four contracts. There are four lines and seven contracts. Six short calls with a stated 100-share multiplier are described as an existing short position of 600 shares. That is a contingent delivery obligation, not evidence of current short stock. Recompute from signed quantities and stated multipliers; do not infer inventory from a narrative summary.

Correct: four position lines comprising seven contracts; six short calls create potential delivery of 600 shares if assigned. An actual separate short-stock line must still be counted.

## Premium, proceeds, loss, and strike funding

Faulty: purchased puts with total premium basis $1,840 and current liquidation value $760 are assigned $12,000 of mandatory strike funding, and their loss is called $1,240. For the isolated fully paid long puts, premium at risk is $1,840; selling now returns $760 and realizes a $1,080 loss before fees. Strike funding must not be imported from short-put assignment. Do not net the realized loss out of the $760 proceeds a second time. Currency labels require an explicit conversion.

Correct: a separate short put may create assignment funding; a long put's exercise can create stock-delivery considerations depending on the account and instructions. Distinguish these from the isolated option's premium loss. A bear put spread opened for a net debit is not a credit spread merely because it contains a short leg.

## Bounded overlay versus independent stock

Faulty: a call vertical has a bounded option payoff, so the report says it caps all upside on separately held shares. The vertical caps its own payoff; unrelated shares retain their own exposure unless the actual combined positions establish otherwise. Check each signed leg and the combined payoff rather than applying a generic “spread” label.

Correct: a short call against owned shares can cap covered-share upside over the relevant range. Do not use the independent-stock example to dismiss a real covered call.

## Reconcile balances, targets, and finished deliverables

Faulty: an account with €2,350 cash receives a €2,650 top-up, but its resulting balance is reported as €2,650 instead of €5,000 before other flows. A table totals 22% international assets while the conclusion says the implemented portfolio reaches 28%. Recompute using the same valuation date, denominator, FX, and transaction set; distinguish an achieved allocation from an aspirational target.

Correct: “Target 28%; proposed implementation reaches 22%, leaving a six-percentage-point gap.” Approximate rounding should not be treated as a material discrepancy.

If the submitted purported final report only says “I will now write the report,” it does not contain the requested deliverable. Flag missing analysis/actions rather than inventing a report or certifying completion. A legitimately labelled progress update is not a false final report. You can assess only submitted text: a clean earlier draft does not certify an unseen final response, and your feedback does not enforce the runtime's completion gate.
