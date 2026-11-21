# 11 — Cash, assignment funding, margin and survival under stress

DRAFT knowledge. The account's ability to survive unfavorable paths is distinct from a trade's modeled terminal payoff. No statement here establishes actual broker capacity.

## Five separate quantities

Cash reservation means setting aside resources intended to fund a possible purchase. Gross assignment funding is the contractual amount payable for the shares or other deliverable if assignment occurs. Economic hedge protection concerns the combined payoff under specified conditions. Broker margin is the requirement imposed under applicable account and house rules. Available capacity is the account's remaining ability to support positions under its broker's current definitions.

These quantities can move differently. A protective put can reduce terminal downside while a short put still creates a gross purchase obligation before coordinated financing or settlement is established. An account may meet initial margin with less cash than assignment would require. A profitable-looking payoff can coexist with a temporary funding shortfall. OIC's cash-secured-put definition and FINRA's margin disclosure support treating funding and broker obligations separately. [S03](../sources.md#s03) [S08](../sources.md#s08)

For standard short puts, gross funding at a specified assignment scenario is the sum of quantity × multiplier × strike, in each contract currency. Premium receipts may be among resources available, but they do not reduce the gross contractual purchase price. Report gross obligation and net economic cost separately. Adjusted deliverables require their own terms.

## Broker requirements and account facts

Initial margin, maintenance margin, eligible equity, excess liquidity, available funds and buying power have different definitions. For example, IBKR's securities account documentation defines excess liquidity as equity with loan value minus maintenance margin, and available funds using initial margin instead. These are requirement cushions, not extra cash balances. Definitions, account segment and timestamp must match the supplied record. [S52](../sources.md#s52)

For a same-definition margin comparison, change in excess liquidity equals change in eligible equity minus change in maintenance requirement. Assignment replaces an option obligation with cash and shares; the disappearing option requirement and new stock requirement, valuation changes, collateral eligibility and other positions can all matter. Gross strike cash is neither of those changes. It cannot by itself establish how much excess liquidity is consumed, whether one assignment is comfortable, or how many additional contracts fit. Cash below gross settlement needs identifies dependence on conversion, sale or borrowing, not proof that those resources are available or that the account is insolvent. Even fresh current totals are not a post-assignment estimate. Current broker projections or a disclosed supported scenario are needed for the dependent capacity conclusion.

FINRA Rule 2264 describes firms' ability to raise house requirements and liquidate assets to address deficiencies without first contacting the customer. A generic regulatory minimum is therefore not a guarantee of the requirement a broker will apply. The user may have no opportunity to choose which asset is sold or to wait for a business thesis to recover. [S08](../sources.md#s08)

Regulation-based and portfolio-margin frameworks differ, and house overlays can change the result further. Knowledge of the framework is not a substitute for broker data or a supported account-specific estimate. If a calculator supplies an estimate, identify its model, coverage and missing house rules. An estimated margin amount must not be relabeled as broker-reported.

A margin-backed short put does not necessarily create a cash loan on the day it is opened. It creates a contingent obligation and margin usage; a debit balance and interest can arise depending on account cash, assignment and other activity. Do not mechanically charge borrowing on the entire strike notional from inception, or assume borrowing is free because no loan existed initially.

## Funding timing, currency and collateral quality

An account can have substantial net assets but insufficient immediately usable settlement cash. Preserve separate native balances and distinguish them from common-currency equivalents. USD 600 plus EUR 400 cannot be labeled native USD 1,000. A converted total needs explicit rates, dates and scope, and must not be added again to the subbalances it already includes. Assets may be restricted, pledged, unsettled or illiquid; conversion has costs and exchange-rate exposure. Distinguish immediately available resources from resources dependent on selling, converting or borrowing under favorable conditions.

Do not assume that cash held at another institution or a personal emergency reserve is available for assignment. Only supplied, authorized portfolio information can support a funding statement. Missing data means unknown, not zero; it also does not justify inventing a transfer.

Collateral and obligations can deteriorate together. A portfolio concentrated in equity shares may lose value while short puts become more expensive and assignment exposure becomes more likely. A hedge can require cash to maintain or monetize, and its broker recognition can depend on terms. An economic offset is valuable but must not be credited twice, across both a stress payoff and an unsupported capacity claim.

## Stress without false precision

Separate terminal-payoff stress from interim valuation stress and broker-capacity stress. Terminal scenarios can show contractual outcomes at expiry. Interim scenarios need a valuation method for remaining options and assumptions about IV, rates, dividends and time. Broker stress additionally needs account rules and house responses. One calculation does not establish the other two.

Consider coherent adverse combinations when relevant: shares decline, implied volatility rises, spreads widen, cash needs arrive and margin requirements increase. The exact combination is hypothetical unless grounded in a defined historical scenario. The purpose is to expose dependence, not to predict the date or assign spurious probabilities.

Liquidity buffers and concentration ceilings are owner decisions, not universal constants. If none are configured, the expert can still describe which exposures could consume resources and why a proposed size depends on missing limits. It cannot mark the unknown limit as passed or invent a comfortable percentage.

## Financing economics and opportunity cost

Compare premium with expected financing and management costs using compatible periods. A high nominal credit may be unattractive if it extends expensive borrowing or blocks capital needed elsewhere. Cash-secured analysis can include interest actually available on reserved assets, but eligibility, liquidity, collateral treatment and yield must be evidenced. Do not assume every broker pays the same rate.

Reducing a position can improve resilience even when it realizes a loss. Conversely, closing everything simply because a mark is negative can be uneconomic if funding is ample and the thesis remains sound. The expert's task is to connect survival, opportunity cost and investment merit, without treating either margin efficiency or maximum cash reservation as a universal objective.

Absent current broker risk data does not erase supported contract obligations or business judgments. A separately supplied owner-authorized planning estimate can provide a provisional scale for a conditional proposal. An owner assumption remains an assumption even when attached to dated NAV or other broker evidence; those records do not establish that the assumed multiplier or capacity was broker-reported. Total indicative planning capacity, unused headroom, immediately available cash, gross assignment funding and transaction-specific option margin are different quantities. Existing positions and collateral can already consume resources, and house requirements or market values can change. Do not infer spare contract capacity by subtracting unrelated measures or treat the planning estimate as a margin model. Conditional sizing remains contingent on relevant broker confirmation and executable terms; uncertainty in that confirmation does not by itself prohibit an otherwise supported preferred recommendation. An unresolved ceiling establishes neither that holding is safe nor that closing everything is necessary.
