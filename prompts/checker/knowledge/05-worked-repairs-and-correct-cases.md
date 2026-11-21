# Synthetic worked checks and valid counterexamples

All companies, identifiers, amounts and dates below are fictional teaching premises, not account context, current observations or recommended policies. Transfer the relationship being tested; do not search drafts for these tickers, demand these thresholds, or copy these trades. A conditionally correct example is not an obligation to use that strategy. Prefer the few findings that materially change the submitted decision.

## 1. Closing a signed position; cash versus historical profit

Aster Devices (fictional ADVC) has +3 calls at strike 70 and −3 calls at strike 85, multiplier 100. Closing requires SELL 3 of the 70 calls and BUY 3 of the 85 calls. Selling the already-short 85 calls adds exposure; calling that action a close or de-risking does not change the resulting inventory. If sale bid minus buyback ask is 4.20, closing receives 1,260. With an original net debit of 1,800, realized P&L is −540 before costs, not a 540 payment needed to close. Reconstruct signed holdings after every order; then compute cash and basis P&L separately. A deliberately larger short position could be a proposal, but must be named and evaluated as such.

## 2. Liquidation proceeds, premium and income are different

An option package costing 760 is sold for 290: cash rises 290 and realized P&L is −470 before costs. Calling the entire 290 income overstates performance. A short option's premium already received is not received again when it expires. Likewise, a roll with opening premium 2, buyback 4 and new premium 5 yields a current credit of 1 and a realized old-trade loss of 2 per share, with a new liability. Check the claimed accounting category, not just the addition.

## 3. A quoted exit condition may already be true

Draft: “Hold the short put; close if its ask is at most 0.90.” Its same-time observation is ask 0.65. The trigger already holds, so the draft needs an actionable close instruction or a stated overriding condition. A stale quote can support “close at a limit of 0.90 or less if still available”; it does not logically support ignoring the trigger. Check all conditions against the draft's own observations and scope. Different timestamps or a genuinely future-only trigger can resolve an apparent conflict.

## 4. Period return is not annual return

Net premium 90 / strike funding 30,000 = 0.30% over 60 days. Simple annualization gives 1.825%; the 30-day equivalent is 0.15%. Calling 1.825% the two-month return is wrong. Identify numerator, denominator, period and annualization convention. None of these calculations establishes realized profit or a repeatable yield; return on margin and return on strike funding are different denominators.

## 5. Contributions have arrival dates

Assume an as-of date of 10 April, with 2,400 arriving at each month end. The first four contributions arrive at the ends of April, May, June and July, not early July. A plan starting in May needs to explain April's omission. Buying a 1,000-face bond from a 600 monthly allocation requires accumulation or separately identified cash; a near-par market price is not necessarily exactly 1,000. Enumerate funded dates and units, not just annual totals. A missed deposit is a scenario, not available funding.

## 6. Owner estimate versus author policy

Owner premise: “Use 80,000 as a planning estimate and challenge it if warranted.” A report must not attribute a mandatory 80,000 target, minimum or binding maximum to that statement. An author may recommend 25% of NAV instead, but concentration arithmetic alone does not uniquely prove that chosen percentage. Identify it as a policy judgment and explain the trade-off. Reject false attribution or a claimed mathematical deduction, not the legitimate act of recommending a different policy.

## 7. Measure reductions against the correct starting exposure

Boreal Systems has 120 shares plus a call spread with 40 delta-share equivalents. Removing the spread removes one-third of the direct-share delta, but reduces combined local delta by 40/(120+40)=25%, not one-third. Additional fund exposure would enlarge the denominator. Contract deliverable count is not option delta. State whether the comparison concerns direct shares, combined local sensitivity or worst-case exposure; do not use the ratio for one to describe another.

## 8. Look through every relevant fund, once

An account has 12,000 directly in Boreal, 24,000 in index fund A, 6,000 in fund B tracking the same index, and 4,000 in global fund C. If Boreal weights are 5%, 5% and 2%, total look-through is 12,000+1,200+300+80=13,580. Omitting B because it duplicates A understates the economic exposure: both holdings count. Unknown fund weights require a partial estimate or range, not an assertion of full coverage. Conversely, adding whole fund values to their constituent values double-counts the same assets. Reconcile both current and proposed portfolios.

## 9. Allocation gaps must agree with the table and the time horizon

A table projects commodities at 6.2% against a 6% target; prose saying it is 1 percentage point short is false even if other sleeves are short. It is 0.2 points above. Recompute actual gaps and distinguish percentage points from relative percent. If current equities are 64,000 in an 80,000 account, a cash-only contribution of 20,000 reduces equity weight from 80% to 64%, not 60%; the denominator grows. Mark fixed-price flow scenarios as scenarios. Known subsequent trades omitted from a baseline must be reconciled separately; do not present that baseline as the current executable portfolio.

A twelve-month projection cannot retain unchanged marks on contracts expiring in five months. Model their expiry cash, exercise/assignment and residual positions, or explicitly exclude that horizon from the projection. A “fixed-price scenario” does not extend a contract's life. Also reconcile a fixed order with its promised target: at NAV 100,000 and equity 54,000, switching 8,000 from equities to cash reaches 46%. Selling six fund shares at 500 moves only 3,000 and leaves 51%. If NAV or market prices change, recompute quantity rather than asserting that a fixed number always restores the target.

## 10. A spread's expiry bound is not perpetual stock protection

Cedar Health (fictional CDHL): short 90 put for 3.20, buy same-expiry 75 put for 0.80, multiplier 100. Net credit is 240; ordinary expiry maximum loss is (15−2.40)×100=1,260 before costs. At expiry stock 82, the short may assign 100 shares bought for 9,000 and the 75 put expires worthless. Mark-to-market loss then is 800−240=560, but retaining those shares exposes the account to further losses after protection ends. If stock is below 75 and both legs settle as assumed, buying at 90 and selling at 75 gives the stated bound. Early assignment can create funding needs while the long put survives; do not assume the broker automatically disposes of both legs as desired.

The correct review preserves the expiry formula while requiring a horizon and treatment of any residual shares for claims about continuing risk. Buying protection does not force an immediate realized loss, eliminate gross short-strike funding, or make holding unhedged shares equivalent to retaining the spread. A covered call later provides only its premium cushion and caps some upside; it does not guarantee recovery. Recommend neither hedging nor refusing a hedge by default.

## 11. Width, intrinsic value and pre-expiry spread value

Long 65 put, short 55 put, net debit 3: maximum ordinary expiry loss 3, maximum profit 7 per share. At terminal stock 60, payoff is 5; below 60 it rises until capped at 10 below 55. Do not call one scenario's payoff the payoff at every lower price.

**Valid counterexample:** before expiry with stock 60, long-put value 8 and short-put value 4 make the spread worth 4, below the long put's intrinsic value 5. The short put has time value, so long-leg intrinsic is not a floor for the net spread. Do not flag that valuation solely from the long leg. Check compatible dated leg prices, net spread relationships and expiry assumptions. This corrects a possible checker false positive rather than prescribing a model price.

## 12. Buying power, assignment funding and economic loss

If supplied broker documentation defines buying power as securities purchasing capacity under particular margin assumptions, do not relabel it as an option-underwriting allowance. A choice not to finance shares is a policy, not a new definition of the broker field. Gross short-put strike funding, current margin requirement, stressed liquidity, and worst-case loss measure different things. Adding a compatible protective put can bound ordinary expiry loss while the short leg's gross assignment funding remains unchanged. Borrowing capacity is not cash, equity or proof of resilience. A contingent external credit line requires availability, timing, currency, interest and repayment assumptions; neither assume it is guaranteed nor forbid it by invented policy.

## 13. A notional cap constrains strike, not stock price or company size

For one standard 100-share put and a 9,000 strike-funding cap in the same currency, strike must be at most 90. The stock itself could trade at 120 with a 90-strike put; whether premium and risk are attractive is a separate question. Market capitalization also depends on shares outstanding, so “large companies are all excluded” does not follow from a low per-share strike ceiling. FX and adjusted deliverables change the calculation. Check the actual binding variable rather than generalizing from a familiar ticker's price.

## 14. Basis does not establish the forward decision

“Hold Aster because selling realizes its loss” supplies no forward investment argument; the economic loss already exists at the current mark. But “sell to reduce concentration” is not cost anchoring merely because price is below basis. Check prospective return, concentration, opportunity cost and relevant tax/cost assumptions. A valuation-supported hold can be valid; the checker must not replace it with a compulsory sale. Similarly, closing a hedge releases cash but removes protection; no underlying shares does not prove the hedge has no portfolio role.

## 15. Unsupported universals and jurisdiction examples

“Every vehicle uses swaps,” “the only inflation hedge,” “the strike is unreachable,” or “losses do not matter unless sold” needs support for the categorical wording. A narrow review can support a narrower statement without proving the universal false. Illustrative tax rates from unspecified jurisdictions do not establish this owner's tax treatment; do not use them to claim a plan's after-tax affordability. Distinguish a labeled sensitivity from a jurisdiction-specific conclusion. Unknown taxes do not by themselves prohibit a pre-tax comparison, but material funding effects need a reserve or explicit contingent assumption.

## 16. Growth, win rates and recovery claims

Earnings rising from 80 to 96 while shares rise from 8 to 12 lowers EPS from 10 to 8. Nine gains of 2 and one loss of 25 produce −7 before costs despite a 90% win rate. Neither business growth nor frequent premium collection proves wealth growth. A claim that premiums offset occasional failures needs loss severity, size, horizon and dependence assumptions. A basket of correlated names cannot be treated as independent merely because it has several tickers. These are support checks, not a demand to predict the future with certainty.

## 17. Check the submitted scope and propagate repairs

A checker corrects “the spread doubles delta,” but another paragraph still says its removal cuts total delta by one-third. The first repair does not cover the second. Check tables, action lists and summaries for the same relationship with changed wording, including debit/credit sign labels. A correctly computed loss can coexist with an incorrectly labeled opening credit. A final author edit or claim never supplied to the checker was not approved by that checker. A reference identifier without its contents is not evidence inspected. State what remains unreviewed rather than granting a report-wide certificate.

## 18. Preserve valid conditional advice

A credible delayed bid/ask can support “sell at net credit at least 2.10 if that limit remains available,” with dated observations distinct from the proposed limit. Missing live execution confirmation does not disprove the thesis, and missing quotes do not prove illiquidity. A public analyst need not know private holdings. A private author can give a conditional plan with a stated funding assumption. Do not turn the examples above into mandatory objections, forced conservatism, or new account rules.
