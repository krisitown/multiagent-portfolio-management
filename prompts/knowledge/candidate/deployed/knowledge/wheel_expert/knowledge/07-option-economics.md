# 07 — Payoffs, cash flows, returns and total economic results

DRAFT knowledge. The formulas here describe simple standard-contract illustrations under declared assumptions. Use deterministic tools for actual proposals, with instrument-specific treatment when the simple model does not apply.

## Short-put arithmetic

Let q be a positive number of short standard contracts, m the multiplier, K the strike, p the opening premium per share, S_T the underlying price at expiry and F the applicable monetary fees. Before interest, taxes and other cash flows:

Opening net credit = q × m × p − opening fees.
Gross share-purchase funding on assignment = q × m × K.
Expiry put profit/loss = q × m × (p − max(K − S_T, 0)) − F, where F is the total fee amount allocated to that expiry scenario.

Before fees, the simple expiry breakeven is K − p. With a stated total fee amount F allocated to this expiry scenario, it is K − p + F/(q × m), equivalently K minus premium net of those specified fees per share. Name which opening, exercise or other fees F includes; unknown fees are not zero. The profit/loss formula has a maximum at the premium less applicable fees and a substantial downside if the shares become worthless. It does not describe the worst interim broker requirement or the cost of closing before expiry. OIC's cash-secured-put material supports the distinction between the reservation of purchase funds and the option's limited premium versus share downside. [S03](../sources.md#s03)

Premium received is cash accompanied by a short-option liability, not automatically equal added wealth. As a nonnegative equity price falls below strike, long-put intrinsic value and the short's positive intrinsic liability increase; signed short payoff becomes more negative. At zero stock price that intrinsic liability is bounded by strike times represented shares. Terminal payoff excludes entry cash; profit includes premium and applicable costs. Assignment changes exposure into shares at contractual strike with prior premium in the economic history; it is not itself proof of investment success or failure.

## Covered-call arithmetic

For q standard calls covered by q × m shares bought at price B, a simplified expiry position result is:

q × m × ((S_T − B) + c − max(S_T − K, 0)) − applicable fees,

where c is call premium per share. Equivalently, the terminal share sale/value component is capped at min(S_T, K), subject to assumptions. Add dividends actually received and financing actually incurred separately. A capped upside does not remove the share downside. These are terminal economic-result identities under the stated basis and cash flows, not a forecast of an early close or assignment path. [S04](../sources.md#s04)

When shares were purchased earlier, separate the full investment history from the incremental decision to sell a call now. A call that improves the next month's cash receipts may lower expected participation in a recovery. Conversely, a willingness to sell at the strike can make the income/upside exchange acceptable. The historical share basis does not decide which assessment is correct.

## Cash, value and capital denominators

Every return ratio needs a named denominator and period. Premium divided by strike notional, actual reserved capital, current equity or an estimated margin requirement describes different quantities. A high return on initial margin can coexist with large losses relative to account equity. Margin can change; using a smaller denominator does not change the contractual obligation.

Simple annualization, for example period return × 365/calendar days, is a comparison convention. It assumes no demonstrated ability to repeat the opportunity and omits path dependence. Geometric compounding imposes stronger reinvestment assumptions. State the method and avoid using either as a promised income rate.

A complete economic comparison can include underlying gains/losses, option gains/losses, dividends, cash interest, borrowing, fees and slippage. Tax is separate unless the required facts and applicable treatment are established. Do not count a stock dividend receipt if the shares were called away before entitlement, or count interest on cash that the broker did not actually remunerate.

## Equivalent payoff intuition and limits

Under a simplified frictionless European-option setup, a covered call and a suitably funded short put at the same strike/expiry can have related terminal payoffs through put-call parity. That connection helps reveal shared equity exposure. It does not make American-style exercise timing, cash requirements, dividends, taxes, borrow costs and broker treatment interchangeable. OIC's parity material provides the theoretical relationship and its assumptions. [S39](../sources.md#s39)

The wheel is a sequence of contingent positions, not a single guaranteed cycle. The sequence may stop at cash, remain in shares, encounter an unwanted event or require a decision while funding is strained. A premium ledger alone omits changes in asset value. Evaluate wealth and obligations at common dates, including open positions, rather than declaring success whenever an option expires or a cash credit arrives.
