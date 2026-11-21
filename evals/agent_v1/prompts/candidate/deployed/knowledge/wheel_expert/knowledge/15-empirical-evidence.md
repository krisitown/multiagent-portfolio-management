# 15 — Empirical research, benchmarks and what a backtest can establish

DRAFT knowledge. This module is interpretive guidance. It does not authorize running evaluations, fetching private histories or tuning prompts against held-out cases.

## Separate a payoff identity from a historical result

A formula can describe a contract payoff exactly under its assumptions. A historical strategy result depends on data, selection, rules, costs and the path realized. An attractive diagram does not demonstrate profitability in practice. An observed return does not prove that a mechanism will persist.

Cboe's PUT materials describe a hypothetical strategy using SPX puts and Treasury-bill collateral. That makes them useful examples of a documented strategy benchmark. They do not describe a discretionary, margin-backed single-stock wheel with physical assignment. Differences in underlying diversification, settlement, financing, contract selection and implementation prevent a direct transfer of performance claims. [S34](../sources.md#s34)

When discussing an empirical result, identify the sample period, instruments, rebalance rules, collateral treatment, transaction assumptions and whether data are live, simulated or backfilled. Without these, a return or Sharpe ratio can be difficult to interpret. A source's marketing summary is not a substitute for its methodology.

## Biases and comparability

Survivorship bias can arise if a historical universe includes only companies that remain successful or listed today. Look-ahead bias can arise from using later filings, revised data or future event information at an earlier decision point. A company that looks fundamentally strong today may not have been known to be so then.

Repeatedly searching many parameters and reporting only the best result can select noise. An apparent delta or DTE optimum should not become an owner policy merely because it won one retrospective comparison. Different periods, costs and market structures can change the result. Report variability and limitations rather than treating one favorable sample as proof.

The benchmark should match the question. A fully collateralized option strategy with substantial cash is not directly comparable with a fully invested equity index unless the different risk and capital uses are explained. A leveraged strategy's return on equity should not be compared with an unleveraged premium yield without accounting for losses and financing. Index total return, price return and strategy cash flow are distinct series.

## Costs and aggregate arithmetic

Sharpe's arithmetic of active management explains that, for a defined market and appropriate dollar-weighted definitions, active and passive holdings collectively share the market's before-cost return; higher aggregate costs lower the active side's after-cost result under that cost assumption. This is an aggregate relationship, not proof that a particular active investor cannot outperform or that every benchmark comparison is valid. [S35](../sources.md#s35)

For this expert, the implication is to demand a coherent reason why a proposed activity is worth its costs and complexity. It does not override the owner's mandate or require passive investing as the answer. It encourages honest comparison and discourages claiming that activity itself creates value.

## Evaluate the distribution and path

Mean return alone can hide severe losses. Win rate omits loss magnitude. Volatility can miss some aspects of skew, jumps or path-dependent funding failure. Maximum drawdown depends on the observed sample and is not a guaranteed future bound. Sharpe ratio also needs a defined return series, benchmark/risk-free rate, frequency and interpretation.

A strategy can have an acceptable terminal expected value and still be unusable if the investor cannot finance intermediate losses. Conversely, a lower average return may be desirable if it better matches obligations and reduces required attention. These are owner-specific tradeoffs, not reasons to cherry-pick a favorable statistic.

A useful research claim specifies what would falsify it. It may survive broad changes in reasonable inputs, or it may depend on a very narrow assumption. Present that dependence. Knowledge acceptance should reward truthful analysis and multiple defensible conclusions, not imitation of a favored trade result.
