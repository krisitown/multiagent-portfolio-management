# 18 — Original synthetic worked cases

DRAFT knowledge. Every company, quote, account and probability below is invented. These are teaching cases, not live opportunities, evaluated production scenarios or held-out answer keys. They demonstrate arithmetic and distinct defensible interpretations. Prices are per share, monetary results are USD unless specified, and contracts are standard with multiplier 100 unless stated otherwise. Taxes, dividends, financing, slippage and fees are excluded unless explicitly included. Unless a case explicitly follows assignment, expiry arithmetic omits early-exercise path effects. Calculator outputs in a live report need their own traceable records.

## A. Premium, fees and assignment funding

Assume two short puts, strike 55, opening premium 1.80 per share, and an opening fee of 1.20 per contract. There are no other fees in this illustration.

- Gross premium: 2 × 100 × 1.80 = 360.
- Opening fees: 2 × 1.20 = 2.40.
- Net premium: 357.60.
- Gross assignment purchase: 2 × 100 × 55 = 11,000.
- Premium-adjusted economic entry: (11,000 − 357.60)/200 = 53.212 per share.
- At expiry spot 50, option economic result: 357.60 − 200 × 5 = −642.40.
- At expiry spot 60, option result: +357.60.
- If shares become worthless at expiry, result: 357.60 − 11,000 = −10,642.40.

The premium reduces net economic cost, not the gross amount paid for shares on assignment. The breakeven convention includes only the specified opening fees. This says nothing about whether 55 is a sensible ownership price or whether the account can finance the purchase. Mechanics background: [S01](../sources.md#s01), [S03](../sources.md#s03).

## B. A credit roll that realizes a loss

Assume one put originally sold for 2.40 at strike 55. It is bought back for 6.10. A replacement put at strike 52, expiring 60 calendar days later than the old contract, is sold for 7.00. The different terms are assumed, not quoted opportunities.

Old-leg realized result = (2.40 − 6.10) × 100 = −370.
Net roll cash flow = (7.00 − 6.10) × 100 = +90.
Cumulative cash received across these transactions = 240 − 610 + 700 = 330.

Immediately after opening the replacement, its own opening receipt is 700, accompanied by a short-option liability at that assumed price. Its standalone premium is 7.00 per share, not 3.30: 330 is cumulative cash after the earlier loss. The old loss has not vanished. If the replacement eventually expires worthless, the combined option result is +330 before costs. If it is bought back for 9.00, the combined result is 330 − 900 = −570, equivalently −370 plus the replacement's −200 result.

Gross purchase funding changes from 5,500 to 5,200 for one assignment, but exposure extends and the replacement can still lose. A credit alone does not decide whether the roll is preferable to closing or remaining in the original position. [S09](../sources.md#s09)

## C. Covered call versus holding shares

Assume 100 shares bought at 50 and one call sold at strike 55 for premium 1.50, same ending date for both comparisons.

At expiry spot 60, covered-call result = 100 × (60 − 50) + 150 − 100 × (60 − 55) = 650. Shares alone return 1,000. The 350 difference is foregone upside net of premium.

At expiry spot 40, covered-call result = −1,000 + 150 = −850. Shares alone return −1,000. The premium buffers 150 of the decline; it does not make ownership low risk.

An owner willing to sell at 55 could prefer the first bargain, while an owner seeking full participation might prefer shares alone. Neither conclusion follows solely from the cash credit. [S04](../sources.md#s04)

## D. Partial hedge, separate funding

Assume two short 55 puts sold for 2.00 each and one long 45 put bought for 0.80, same issuer and expiry. Net premium = 400 − 80 = 320.

At expiry spot 30, short-put liability at payoff is 2 × 100 × 25 = 5,000; the long-put payoff is 100 × 15 = 1,500. Net option result = 320 − 5,000 + 1,500 = −3,180.

One long protects only one contract's matched payoff exposure. One short remains unmatched. Gross short-put purchase obligations total 11,000 if both are assigned. The long's economic payoff does not by itself prove that the broker will supply funds, coordinate exercise or recognize a particular margin offset. [S01](../sources.md#s01), [S08](../sources.md#s08)

## E. Current equity, stressed equity and broker requirements

Invented current account: noncash assets 96,000, separately held cash 10,000, short-option liabilities 6,000. Equity = 100,000. In one hypothetical interim stress, noncash assets fall to 67,200 and the option liabilities rise to 18,000 while cash stays 10,000. Stressed equity = 59,200.

Assume, purely for comparison, one broker requirement estimate of 52,000 and another of 62,000 under different house assumptions. Surpluses relative to these estimates are +7,200 and −2,800. Neither estimate is actual broker information.

A separate synthetic assignment illustrates the distinction. One short 50 put on 100 shares is valued at its 500 intrinsic liability with stock at 45. On assignment, cash changes by −5,000, shares worth 4,500 arrive and the 500 liability disappears: the immediate net-wealth change is zero in this frictionless fixed-price illustration. The earlier economic loss has not vanished. This is not an eligible-collateral or margin calculation: the stock's broker treatment and replacement requirement remain unspecified. Likewise, 33,000 gross settlement cash against 10,000 cash identifies a 23,000 funding dependency, not the margin deficit. Neither subtracting 33,000 from excess liquidity nor adding excess liquidity to cash produces a supported capacity estimate. [S08](../sources.md#s08) [S52](../sources.md#s52)

## F. Dividend-sensitive call

Assume a call has 0.20 of remaining extrinsic value and the underlying has an announced 0.80 dividend with an approaching ex-date. This may make early exercise relevant for a holder, but the comparison is not a calibrated assignment probability and omits financing and holder-specific conditions.

A covered-call analysis that counts both continued share ownership and the dividend must show the alternative in which shares are called away before entitlement. It cannot declare that assignment is certain, or that covered shares make the dividend safe. [S02](../sources.md#s02)

## G. Share coverage cannot be reused

An invented account holds 150 eligible shares and is short two standard calls requiring 200 shares if both are assigned. The aggregate shortfall is 50 shares. The 150 shares cannot be used twice to label both calls fully covered.

Adding one long put does not fill the 50-share delivery gap: its exercise sells shares. Adding one compatible long call instead may provide an option hedge, but still leaves exercise funding and timing to establish; it is not 100 already-owned shares. With 250 eligible shares and the same two calls, 200 can be allocated once and 50 remain, subject to other commitments. Changing the signed inventory changes the result; a rough delta offset never proves delivery coverage.

## H. Valuation sensitivity

Assume a mature nonfinancial business with next-year FCFF of 6 million, constant growth 2%, WACC 10%, net debt 15 million, 3 million shares, and no other bridge adjustments.

Operating value = 6/(0.10 − 0.02) = 75 million.
Equity value = 75 − 15 = 60 million.
Value per share = 60/3 = 20.

With growth instead 3%, operating value becomes about 85.714 million and per-share equity value about 23.571. With WACC instead 11% and growth retained at 2%, operating value is about 66.667 million and per-share equity value about 17.222. These are separate sensitivity changes, not simultaneous changes.

The range reflects assumptions, not confidence bounds. A put effective entry of 19 could look attractive under one scenario and expensive under another. The inputs must be economically supported before any ownership judgment. [S17](../sources.md#s17)

## I. Yield denominator and financing

Assume a 300 option profit over 30 calendar days, 10,000 allocated capital, and 100,000 starting account equity. Before other changes, the period return is 3% on allocated capital and 0.3% on account equity. Simple annualization of the former is 3% × 365/30 = 36.5%; it is not a forecast.

If that 10,000 was actually borrowed for all 30 days at a simple annual 8%, financing cost is about 65.75. After that cost, the isolated result is about 234.25, or 2.3425% of allocated capital. This borrowing is an explicit invented assumption; opening a margin-backed put does not necessarily create that loan.

## J. High win rate, negative expected result

Assume an invented one-period distribution with 90% probability of +100 and 10% probability of −1,500. Expected result = 0.90 × 100 − 0.10 × 1,500 = −60.

A 90% win rate therefore does not establish positive expected value. The assumed probabilities are not market estimates, and the calculation does not capture funding failure or how uncertain those probabilities might be. It simply illustrates why severity matters.

## K. Currency can reverse the reported result

Assume a USD investment earns 5%, while each USD buys 10% fewer units of the investor's reporting currency by the end. Reporting-currency return = 1.05 × 0.90 − 1 = −5.5%, before costs. Adding 5% and −10% would miss the compounding cross term.

This does not identify the user's base currency or recommend a hedge. It shows why account and contract currencies must remain explicit. [S32](../sources.md#s32)

## L. A good company with unusable current evidence

Suppose issuer filings support a resilient business, but the only available option quote predates a material announcement and broker capacity is missing. The expert can retain the company as a research candidate, explain its ownership thesis and show clearly assumed one-contract economics. The margin-backed proposal remains unverified because the missing facts affect that proposal; this description is not a mandatory status label.

If later evidence shows a fresh quote but the portfolio would breach a configured issuer limit, the known barrier should accompany the proposal as a condition. A different strike does not automatically solve concentration. A smaller position might help, but only a recalculated combined exposure supports that claim.

## M. Two defensible responses to the same loss

A synthetic company has fallen after disappointing earnings. In version one, later evidence supports a temporary margin issue, ample financing and an owner with available funding and acceptable concentration. Holding may be defensible. In version two, the same price history is paired with deteriorating liquidity, a weakened business thesis and imminent cash needs. Closing or reducing may be defensible.

The example deliberately supplies no preferred roll, delta or expiry. Price history alone does not determine the decision. The expert's work is to identify which premises actually hold and explain the consequence, not to match a memorized case conclusion.

## N. One cash pool, two displays; close cash versus book loss

Assume the entire cash inventory is USD 6,000 plus EUR 4,000. At an illustrative 1.10 USD/EUR, a report also displays total cash of USD 10,400 equivalent. That total is 6,000 + 4,000 × 1.10, not a third balance. Converting all EUR at that same rate, with no costs, leaves USD 10,400 and EUR zero; it does not create USD 14,800. If another USD 3,000 is separately documented as a distinct settled cash balance, the total instead becomes USD 13,400. An unidentified total cannot be assumed distinct. Neither case establishes broker eligibility, unrestricted collateral or borrowing capacity.

Separately, assume one long option has a supplied aggregate broker book cost of USD 700 and an observed bid of 4.00 per share for the standard multiplier 100. A hypothetical sale at that bid brings in USD 400 before costs and gives a book result of 400 − 700 = −300 under the supplied convention. The USD 300 loss is not cash payable to close this long. For a separate short with bid 3.80, ask 4.20 and broker mark 4.00, an ask-based one-contract repurchase illustration requires USD 420 before costs. If its documented current-leg opening receipt was USD 250, the illustrated close result is −170; the 170 loss is not the 420 debit. A book result at the stated 4.00 mark would instead be −150 under that simple convention. Missing earlier-cycle history does not erase the known 250 current-leg receipt. If that receipt too is unavailable, historical P&L remains unknown while the 420 closing illustration remains usable. These are assumed transactions at supplied illustrative sides, not guaranteed fills or tax results.

## O. The option ends; what remains?

Assume one short American put, strike 50, expires before a later company event. If it expires unassigned, its obligation ends with no shares created. If assigned, purchase cash is 5,000 and 100 shares remain unless an evidenced subsequent transaction changes that holding. Those shares are exposed to the later event. A report cannot count the assignment entry benefit in one comparison while claiming no post-expiry equity exposure in another. A funded owner may accept that exposure; another may reasonably prefer avoiding it. Neither choice is dictated by the calendar.

Separately, assume standard compatible calls: long two June 45 calls, long three December 40 calls and short three December 55 calls. June expires with spot 30 and its calls unexercised. The December pair survives. At December spot 60 its terminal payoff is 100 × [3 × (60 − 40) − 3 × (60 − 55)] = 4,500. Its terminal payoff ranges from zero to 4,500; with an assumed 600 entry debit for that pair, profit ranges from −600 to 3,900 before costs. The earlier calls' costs are outside that pair's result. Their expiry does not expose an unlimited December upside loss. Removing one December long instead gives 2,500 payoff at spot 60 and an unbounded negative tail, losing another 100 for each further unit of spot. Moving the protective longs' expiry before December changes the surviving rights and cannot preserve that same-expiry bound by assumption. Terminal payoff is not a pre-expiry mark, exercise funding or broker recognition; early assignment and independent exercise can change the path. [S02](../sources.md#s02) [S50](../sources.md#s50)

## P. A conclusion cannot become more certain than its source

Assume retained issuer text describes a conditional annual operating-cash-flow sensitivity of +12 million for a specified commodity-price increase, all else equal. It supports that sensitivity. It does not establish a +12 million free-cash-flow forecast: the price path, capital expenditure and other cash-flow changes are not supplied. If only an analyst's paraphrase is available, even the attribution remains dependent on that report; a citation alone is not inspected source text.

A provider calendar labeled estimated for 20 May supports an estimated date. A separately retained issuer announcement dated 1 May specifying 24 May supports an announced scheduled date as of that announcement, still subject to later change. Changing only the evidence changes the permissible wording; fetching the same estimate again does not confirm it. Likewise, three quoted contracts support comparison among those observed contracts, not a claim that no other strike or expiry is better. More confident wording must follow stronger evidence, not a more fluent summary.

## Q. Curve direction must match the displayed prices

Assume same-time USD-per-unit prices for one commodity/specification: spot 70, near future 72, farther future 75. The displayed curve rises with maturity (contango). With spot 75, near 72, farther 70, it falls (backwardation). Neither configuration predicts the later spot price. If, solely for illustration, the far contract converges to unchanged spot, long-futures price change per unit is 70 − 75 = −5 in the first case and 75 − 70 = +5 in the second. Changing the price path can change either result.

These conditional convergence amounts are not guaranteed roll returns or an immediate cash payment equal to the near/far gap when a position is rolled. Realized futures results also depend on contract selection, changes in prices, collateral and costs. A commodity-unit price is not a producer's share price, option strike or company cash-flow forecast. Identify the instrument and horizon before carrying the curve interpretation into an equity thesis. [S45](../sources.md#s45)
