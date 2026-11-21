# 13 — Hold, close, roll, assignment and covered-call decisions

Management reference revised 2026-10-31 under the owner's authorization. This module describes decision dimensions, not a trading algorithm. Several actions may be defensible under the same uncertainty; the expert should explain the tradeoff and the premises supporting its preference.

## Establish what is currently held

The original readable portfolio governs held inventory; analyst narratives remain attributed research. Keep signed quantity and exact contract terms when identifying a current position. Missing or conflicting terms limit the dependent conclusion rather than authorizing an invented holding. The control and evidence module govern reconciliation, source scope and local uncertainty.

`finance_calculate` supports `match_coverage` and `analyze_portfolio` with a supplied snapshot reference or explicit supported position terms, base currency and as-of date. A subset calculation has only subset scope; success does not establish full-account balances, NAV or margin. The matcher allocates a compatible long once and retains strike-gap exposure. It does not support explicit/adjusted deliverables or certify coordinated exercise, funding or broker recognition. Unsupported matching is not proof of absent economic protection; the contract-identity module describes conditional geometry.

An allocation belongs to its exact short position. Aggregate `unmatched_short_contracts` includes calls as well as puts, and unmatched by this method does not mean cash-unbacked. Other expiries cannot be subtracted by ticker as same-expiry contractual protection. `analyze_portfolio` short-put assignment funding is gross settlement cash, before hedge, premium or margin offsets. An unavailable aggregate remains unknown even when partial currency details exist. Do not invent IDs, allocations or a snapshot, treat partial/unsupported output as a complete result, or equate terminal protection with early-assignment funding. [S02](../sources.md#s02) [S03](../sources.md#s03)

Shares establish a holding, not its acquisition route. Describe assigned wheel stock only when assignment or lot history supports it; a price near an old strike or a disappeared put is an inference. Use supplied broker basis under its stated sign, currency, unit and scope. Aggregate book cost/credit is not a per-share premium to multiply again, and a broker mark is not an opening receipt. Known open-position basis can support its own accounting result even when earlier rolls, transaction fees, tax lots or full-cycle P&L remain unavailable. Missing historical context does not erase supplied fields; unknown fields remain unknown. Continue useful current-position analysis without inventing history.

## Compare evidenced alternatives with holding

An adjustment needs a defensible comparison with holding the actual present exposure. Use observed quotes for each existing or proposed leg that materially affects the comparison, identifying the exact contract, bid/ask or other selected field, quantity, currency, source and actual as-of time. The latest available outside-hours quote can support a dated analysis; its age, missing fields and intervening events still constrain the conclusion. A quote from one expiry cannot price another expiry. A midpoint or model estimate is not an observed executable quote. Missing quotes leave an alternative unpriced, not a recommendation supported by an invented premium. The finance calculator accepts supplied inputs; its successful arithmetic does not verify their quote provenance or timestamps. Check those against the supplied evidence.

Compare net transaction costs and changed risk over stated, compatible horizons, including fees and spread effects where evidenced, remaining obligations, ownership outcomes, concentration, financing and management burden. Unknown costs are unknown, not zero. Use the available finance formulas only within their supported inputs and assumptions. The current `roll_economics` supports equal-count, equal-multiplier close/reopen cash with optional `original_credit`. Omit unknown original credit; never replace it with zero or a current mark. Incremental roll cash can remain calculable while old-leg realization and cumulative-history cash remain unknown. A supplied broker book basis may support a separate book-result comparison under its stated convention, without proving historical opening premium or tax basis. A calculation outside the exposed tool's supported inputs remains unsupported. An authorized research request can address missing public inputs; it does not add a formula or certify an assumption. Do not invent a calculation or tool. An illustrative sensitivity can explain a dependency without replacing a missing observed quote or establishing an actionable net advantage.

A management thesis can use fundamental valuation, but its inputs need their own evidence. A forward EPS estimate needs a source, fiscal period, definition, currency and as-of date. Do not replace missing forward EPS with an unsupported value to make a put strike or a roll appear attractive. An explicitly assumed forecast remains a scenario, and contrary analyst findings remain visible in the final judgment.

An account-margin PDF is dated reported evidence. Changed-condition projections are scenarios, not current broker capacity; the cash/funding/margin module explains the required distinctions. Missing capacity limits dependent proposals without erasing known obligations. [S08](../sources.md#s08)

## Reassess the exposure from today

A position's history explains how it arose and may matter for tax or performance, but the next decision changes future exposure. Consider whether the business thesis, valuation, portfolio role, funding and event risks remain acceptable. A loss does not prove that the thesis is broken; a profit does not prove that continued exposure is wise.

Doing nothing preserves the current position and its obligations. It is a real alternative with costs and risks, not an absence of analysis. Waiting can avoid unnecessary spreads and preserve optionality, but it can also leave an approaching obligation unresolved. Distinguish deliberate inactivity from inattention.

Closing removes or reduces the relevant exposure once completed, potentially realizing a gain or loss and freeing resources. The cost should be compared with the value of reducing risk and the best available use of capital. Do not reject closing solely because it makes a loss visible.

## Assignment as a change in ownership

A short-put assignment can be an acceptable way to acquire shares if ownership and funding remain supported. It can also create unwanted concentration or borrowing when the original conditions have changed. Willingness expressed at initiation must be reassessed against material new evidence.

Accepting assignment is not necessarily an action the writer can choose unilaterally. The expert can analyze leaving the short open and its assignment scenarios, but the holder's exercise and allocation determine the event. Avoid phrasing that implies a guaranteed assignment on demand.

Once shares are held, their future risk depends on their present value and prospects, not only a premium-adjusted historical basis. Selling calls may be appropriate, but the wheel sequence does not require it. A recovering company can have unattractive call pricing or an owner who prefers uncapped upside. A deteriorating company can warrant reduction rather than repeated call sales that postpone a decision.

## Rolling is a replacement decision

A roll combines closing an existing option with opening another. The old leg's realized result uses its own opening and closing cash flows; the replacement has its own opening basis and unresolved future result. For equal quantity and multiplier in one currency, let O be the old opening receipt, C the current repurchase payment, and N the replacement opening receipt, all positive aggregate amounts before costs. Old-leg realization is O − C; incremental roll cash is N − C; cumulative option cash is O − C + N. Adding old-leg realization to incremental roll cash subtracts C twice. While the replacement is open, cumulative cash omits its remaining liability and is not realized campaign profit. If it expires worthless without further trades or costs, cumulative cash becomes the combined option result; versus closing only, that conditional improvement is N because C is common to both alternatives. A replacement short put's standalone expiry breakeven is its strike less N divided by the total shares represented by all replacement contracts (quantity × standard share multiplier). If assigned, the corresponding campaign economic acquisition cost per share subtracts cumulative cash divided by that same total acquired share count instead, with applicable history and costs; it is not broker tax basis. Different quantities, adjusted terms or missing history require separate leg-specific treatment rather than this shortcut. Reported comparisons and breakevens retain their scope and outcome assumptions; a favorable worthless-expiry comparison is not a forecast or guaranteed advantage. For a short, closing debit is repurchase price × closed quantity × premium multiplier plus costs, not the loss relative to opening credit. Broker unrealized P&L uses its stated mark/basis convention and may differ from an ask-based closing illustration. A successful calculator cannot validate a mislabeled bid or basis. [S09](../sources.md#s09) [S48](../sources.md#s48)

Compare the replacement with a fresh trade made from a flat position, then account for the cost and consequences of closing the old one. A favorable credit may result from extending time, increasing risk, moving the strike, changing quantity or accepting poorer liquidity. A debit roll can sometimes reduce risk; a credit roll can sometimes increase it. The sign of the cash flow does not settle the judgment.

For multi-leg or size-changing rolls, each purchase (closing a short or opening a long) consumes cash and each sale (closing a long or opening a short) produces cash, with its own multiplier, quantity and currency. Keep old/new identities, dates, fees, remaining old exposure and changed obligations. Sum only compatible currencies or explicitly convert. An incomplete close leaves old contracts open; a larger replacement credit may buy extra risk. Use supported arithmetic rather than forcing these legs into an equal-count single-option formula.

A roll is not always available. Quotes can widen, strikes or expiries may be unsuitable, financing can be constrained and events can change the thesis. A strategy whose safety depends on always rolling for a credit relies on an unsupported future opportunity.

Keep current, intervening and replacement obligations on their actual dates. Opening a later-expiry position does not close an earlier short; a proposed replacement changes the earlier obligation only conditional on completing the specified close. Choosing a later entry date does not remove obligations that remain open before or after that date. Later expiry also does not eliminate early assignment on an American-style short. Do not treat calendar extension as proof of released funding, event avoidance or resolved near-term exposure. [S02](../sources.md#s02)

## Covered calls and deliberate sale prices

A covered call exchanges some potential upside for premium while retaining meaningful equity downside. Ask whether selling shares at the strike over the term would fit the owner's objective and whether the premium justifies the foregone flexibility. Neither selling below historical basis nor refusing every below-basis strike is universally correct.

If the stock has materially changed, the appropriate call strike may differ from the price needed to “get back to even.” A historical loss can be real without making that recovery target a sensible future choice. At the same time, taxes, ownership plans and concentration can make the history relevant. Keep these distinct rather than discarding or overvaluing it.

Verify share coverage and existing encumbrances. A long call is not automatically the same as owned shares for exercise funding, dividend entitlement or broker treatment. If a covered-call comparison depends on dividends, include the assignment interaction and actual entitlement assumptions.

## Compare burdens as well as payoffs

An active adjustment can add research, monitoring, tax-lot complexity, spread costs and further decisions. The marginal economic advantage should be weighed against that burden without inventing a monetary value for the owner's time. “No change warranted” is appropriate when available evidence does not justify an adjustment.

An unpriced alternative cannot support an execution-ready price claim, and unverified capacity limits proposals dependent on that capacity. These gaps do not erase a supported comparison of other alternatives or the risks of holding. State what can still be assessed and which conclusion remains conditional; missing evidence is not proof that holding is safe or economically optimal.

Consider urgency and impact when deciding what needs attention. A contract-identity error, imminent funding problem or uncovered obligation may deserve attention before a small theoretical improvement. Urgency is not permission to bypass evidence or produce an order. If facts are missing, identify the specific consequence and the information needed.

Make the favored alternative and its decisive assumptions understandable, including what would change the view. No fixed comparison structure or exhaustive strategy list is required.
