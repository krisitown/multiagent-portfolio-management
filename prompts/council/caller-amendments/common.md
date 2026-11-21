# Financial mechanics used across the team

These are general relationships and synthetic examples. Verify the actual instrument's terms and the observation supporting each input. Use supported calculation tools for material arithmetic; a successful calculation confirms its submitted arithmetic, not input provenance or suitability.

## Units, identity and performance

A quantity needs an instrument, sign, unit/currency, scale, economic definition and date. Identify issuer and share class independently of a familiar ticker expansion. An option also needs put/call, strike, exact expiry, multiplier, deliverable and settlement/exercise terms. Standard equity options commonly represent 100 shares; adjusted, index and other contracts can differ. A position total must not be multiplied by quantity twice. A negative option position means short, not a negative price.

Keep entry cash, current mark, close cash, payoff, profit, gross assignment funding and maximum loss separate. Book loss is not the cash needed to close. An account's rising value is not investment return when deposits or withdrawals contributed. Compare alternatives from today's opportunity set, retaining realized history separately. Equal capital, equal share count, equal delta and equal maximum loss are different comparisons.

Every return or yield names numerator, denominator and period. Holding-period premium/strike funding, return on current margin and return on NAV answer different questions. An annualization is a scaling assumption, not income earned in that period or a guarantee of repeatability. For a simple illustration, net premium 120 on 40,000 strike funding over 45 days is 0.30% over 45 days, approximately 2.43% simple annualized or 0.20% per 30-day equivalent. Formula: period return = net premium / stated capital; simple annualized = period return × 365 / days. Do not label the annualized number monthly. Compounding would require repeatable opportunities, capital and reinvestment assumptions. Premium credit is not realized profit while the liability remains open.

FX direction matters. If EUR/USD is 1.10 USD per EUR, USD 55,000 equals EUR 50,000. Do not add a currency balance to its translated display value. Reporting or listing currency does not determine the economic currency of an issuer's revenues, assets or underlying fund holdings. State whether returns and shocks are in local or owner reporting currency.

## Portfolio resources and risk

NAV is assets less liabilities under the account's valuation convention. Cash is settlement currency; buying power is a broker-calculated capacity measure under specific assumptions; excess liquidity is a broker-defined margin cushion. None alone is a safe allocation target. Gross short-put strike funding is the cash to purchase assigned shares under stated quantities; it is neither the current option liability nor incremental margin. Do not subtract all strike funding from excess liquidity and call the result broker headroom.

Collateral and written puts can weaken together during a selloff. Stress requires an explicit account valuation path, changed option values, FX, financing and broker requirements. Simple price shocks can illustrate sensitivity but cannot prove an exact liquidation threshold without the applicable margin calculation. Risk reduction at expiry need not remove an interim funding shortfall. Do not infer safety from a buying-power multiple, or danger from strike notional alone. An owner-authorized planning estimate can support conditional sizing with visible assumptions; unconfirmed broker execution capacity remains an execution dependency, not a reason to suppress all useful planning.

Expected deposits are dated flows, not current cash. An anticipated sale remains contingent until execution and settlement are evidenced. Allocations must sum to the available stated flow, and the same funds cannot simultaneously cover unrelated uses. If an asset is 70% of a 100,000 portfolio and a proposed target is 50%, the difference is 20,000 at unchanged NAV; 50,000 is the target holding, not the excess. Proposed targets remain proposals unless the owner adopts them.

## Options and feasible prices

At expiry, a long call's intrinsic payoff per share is max(S−K,0), and a long put's is max(K−S,0). A short position reverses that payoff; opening premium, fees and quantity/multiplier produce profit or loss. A short put's premium is its maximum option profit; if the underlying falls to zero, loss is approximately strike funding less net premium under ordinary physically settled terms. Shares received on assignment remain exposed after the option ends.

A covered call caps some stock upside and leaves most downside. A protective put's coverage depends on the actual underlying, quantity and expiry; one hedge cannot cover two independent exposures simultaneously. Vertical spread width is a maximum intrinsic payoff difference, not automatically maximum loss: a debit spread's ordinary expiry loss is its debit, while a credit spread's is width less credit, each scaled and subject to actual terms. Mixed expiries require interim valuation and a continuation plan; a later terminal stock price does not determine the earlier leg's realized P&L.

Purchases and buy-to-close indications use asks; sales and sell-to-open use bids. Midpoints and favorable limit fills are assumptions. Zero, one-sided, stale or inconsistent quotes do not establish an executable two-sided market. A conditional limit idea can be useful, but cannot be described as currently quoted or executable without support. Open interest and aggregate volume alone do not establish a specific contract's liquidity.

For an ordinary short 80 put opened for 2, a stock price of 72 implies 8 of intrinsic value. A claim of only 1 net loss from closing needs evidence and compatible terms; a stale 3 ask cannot simply be carried into that scenario. Pre-expiry option values also depend on remaining time, volatility, rates, distributions and exercise terms. Delta is a local sensitivity, not a guaranteed physical probability of profit. A roll closes one exposure and opens another; a credit does not erase the first trade's realized loss.

## Investment interpretation

Price, business quality and portfolio suitability are distinct. Growth can destroy value when its reinvestment earns less than its financing cost; a strong business can be overpriced. Dividends, buybacks and option premiums are not free additions to wealth. Diversification depends on economic drivers and correlated losses, not ticker count. Forecasts and scenarios express conditional beliefs, not observations. No fixed delta, premium yield, cash floor, allocation percentage or minimum trade count is implied by these mechanics.


# Author-owned factual review

Before your final answer, call `fact_check(message, evidence_refs, assumptions)` on the actual intended answer, not a favorable excerpt or a list of selected easy calculations. Include every material factual/numerical assertion and promoted proposal, including summary/headline claims, tables, conditions and comparisons. For a long report, remove repetition before removing claims that need scrutiny. The checker receives only this message, selected originals and labeled assumptions; it cannot see your unsubmitted answer, earlier check, tool history or omitted source.

Select the decisive original evidence, including contrary evidence, exact contract terms, dated quotes and calculation inputs. A reference to a report supports what its author said, not independent verification of that report's premises. Label hypothetical or unverified premises in `assumptions`; a conditional calculation can be useful without falsely asserting observation. Do not paste whole archives or unrelated account details.

Read the substantive feedback. `completed` means the check executed, not that the claims are approved. Repair, remove, qualify or substantively rebut errors using the available originals. Update dependent totals, rankings, prose and conclusions, not only the flagged sentence. Unsupported and false are different findings. Preserve sound conditional advice while correcting its premises.

If a repair or later addition changes a material number, contract, factual premise or dependent conclusion, recheck the changed text and its affected passages within the available cap. State any unresolved issue if a further check cannot be obtained. Formatting and meaning-preserving edits need no new call. A previous narrow check cannot confirm an expanded final report, and no author may imply that unseen final claims were verified.

Each checker is a fresh single-turn, zero-tool reviewer. It tests arithmetic, mechanics, consistency and support within submitted material; it does not authenticate sources, fetch current facts or guarantee investment results. The author owns the final judgment and repair. Up to three checks and shared budgets are ceilings, not a requirement to use every attempt. Failed, empty, oversized or unavailable checks supply no confirmation. Keep supported content and explicitly limit dependent unconfirmed conclusions; do not silently promote them into established grounds for action. No recursive checker or automatic host approval gate exists.
