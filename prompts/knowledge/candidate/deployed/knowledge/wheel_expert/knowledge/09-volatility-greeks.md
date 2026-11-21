# 09 — Volatility, Greeks and probability limitations

DRAFT knowledge. Models summarize relationships under assumptions. They help reason about exposure; they cannot convert uncertain outcomes into facts.

## Implied and realized volatility

Realized volatility describes variation in observed returns over a specified sample and method. Implied volatility is inferred by fitting an option-pricing model to a market price. The two answer different questions. Quote quality, model choice, dividends, rates and exercise treatment can affect the inferred value. OIC's materials distinguish historical and implied volatility and discuss their interpretation. [S43](../sources.md#s43)

The level of implied volatility is not a volatility risk premium. That premium concerns implied versus expected realized volatility or variance, with a specified measure and compatible horizon; raw IV alone establishes neither its sign nor its adequacy. High IV can reflect real jump or tail risk. Low IV establishes neither cheap ownership, low convexity nor safe capital preservation. [S43](../sources.md#s43)

Historical IV rank or percentile requires a compatible implied-volatility history. A series of underlying prices supplies historical realized volatility, not historical IV rank. Rank often compares current IV with a historical range; percentile often describes its position among historical observations, but vendors can define them differently. State the formula, window, underlying option selection and missing-data handling before interpreting the number. Schwab's terminology provides one provider-specific example, not a universal standard; do not silently remap a vendor field. [S43](../sources.md#s43)

Skew compares implied volatility across strikes, while term structure compares expirations under a chosen convention. Higher IV on lower-strike equity puts is compatible with ordinary downside skew; a smile need not peak at the money. Quote quality, event timing and protection demand can affect both. A stock's single IV headline need not describe the contract being considered, and a higher-IV strike can represent different tail exposure rather than a bargain. [S43](../sources.md#s43)

## Local sensitivities

Delta estimates the local change in an option value for a small change in the underlying, holding specified inputs constant. Gamma describes the local change in delta. Theta represents time sensitivity under a model convention. Vega represents sensitivity to implied volatility, commonly quoted per one percentage-point change, but units require verification. Rho concerns interest-rate sensitivity. OIC provides separate educational treatments of these Greeks. [S05](../sources.md#s05)

Under usual vanilla-option pricing assumptions, an ordinary long put generally has negative delta and nonnegative gamma and vega; a short put reverses the position exposures. A held short option generally benefits from falling IV through a lower repurchase liability, all else equal, while its long counterpart loses through that component. Lower premiums on future sales are a separate effect. Underlying moves, time and other changes can outweigh the volatility effect. Low delta or distant terminal moneyness does not establish negligible interim mark value: local dollar change, percentage change relative to premium and payoff at expiry are different quantities. [S51](../sources.md#s51)

For compatible per-share long-option sensitivities g_i, signed contract quantities q_i (long positive, short negative) and premium multipliers m_i, position sensitivity is Σ(q_i × m_i × g_i). A provider field already aggregated or position-signed must not be multiplied or signed twice. Common units, valuation dates and the relevant underlying/volatility shock matter; cross-expiry parallel-volatility aggregation is an assumed scenario, not a claim that every expiry moves equally. Four long contracts with per-share vega 0.03 and one short with vega 0.20, each multiplier 100, have −8 currency units per parallel one-percentage-point IV rise: 100 × (4 × 0.03 − 0.20). More longs than shorts do not establish positive net vega or gamma, and missing leg sensitivities leave that aggregate unresolved. A delta-based share equivalent is not shares certain to be assigned. Gamma changes delta, and a delta-neutral book can retain jump, liquidity and funding risk.

Time decay is not a guaranteed daily cash receipt. A favorable theta at one snapshot is not tomorrow's income. Event resolution can reduce IV, but does not establish a particular post-event IV or percentage premium loss. Even a supported IV scenario needs the relevant sensitivity or repricing assumptions to describe its price effect; a large move can invalidate a local approximation. Do not present an illustrative sensitivity as a forecast or infer a premium percentage directly from an IV percentage.

## Probability language

Under a standard non-dividend European Black-Scholes setup, call delta involves N(d1), whereas the model's risk-neutral probability of expiring in the money involves N(d2). Their difference illustrates why even within a familiar model delta is not literally the same object as an in-the-money probability. For American options with dividends and early exercise, an informal delta-as-probability interpretation needs further caution. Damodaran's lecture slide states the two quantities directly; OIC supplies broader model limitations. [S44](../sources.md#s44) [S06](../sources.md#s06)

Risk-neutral probabilities are pricing constructs under model assumptions, not automatically forecasts of real-world frequencies. Assignment probability is another object: it depends on exercise decisions and allocation, including before expiry. “Probability of profit” depends on the defined breakeven, fees, horizon and management rules. A vendor percentage with no documented method cannot bear a stronger interpretation than its provenance supports.

It is possible to provide a useful judgment without a precise probability. A range of scenarios and the conditions that make each consequential may be more honest than a number with many decimals. If probabilities are supplied for educational expected-value calculations, label them as assumed or estimated and test sensitivity. Do not imply that their sum to one validates them.

## Tail risk and empirical claims

A return distribution can be asymmetric, with frequent small gains and occasional large losses. Win rate alone omits severity and path. Selling an option transfers exposure; a good historical hit rate does not establish that current premium fairly compensates it. Correlation can also behave differently in stressed conditions, affecting simultaneous losses.

Claims about a volatility risk premium or the historical success of option-selling require an identified study, universe, period and implementable cost assumptions. Index-level evidence does not automatically establish the same result for a few single stocks. Do not turn an educational model or a backtest into a claim about the owner's expected return.

Useful outputs connect sensitivities to decisions: how an adverse move could change exposure, why IV uncertainty affects closing cost, and whether the portfolio can tolerate a jump without forced action. They should not bury the investment thesis under precise-looking model fields that the evidence cannot support.
