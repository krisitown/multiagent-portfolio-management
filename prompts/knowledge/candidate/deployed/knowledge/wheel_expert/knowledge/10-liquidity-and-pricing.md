# 10 — Quotes, liquidity, spreads and feasible transactions

DRAFT knowledge. A research proposal is useful only to the extent that its market data represents a meaningful opportunity. This does not authorize execution.

## What a quote says

A quote includes a source, observation time, fetch time, timezone, delay status, bid, ask and available size. Its underlying quote may have a different timestamp. A current download can contain delayed observations. A last trade can be old or occur under conditions no longer available. Preserve those distinctions rather than calling every displayed number “the price.”

OIC's liquidity guidance discusses bid/ask spreads and size and cautions against treating volume or open interest as sufficient evidence of liquidity. Open interest is outstanding contracts, not a promise of current buyers or sellers. A busy underlying can still have thin individual option series. [S07](../sources.md#s07)

Quote side follows the transaction, not put versus call. Buying to close a short uses a seller's ask as a repurchase illustration; selling to open a replacement short uses a buyer's bid as a sale illustration. Selling a long to close likewise uses bid, while buying a long uses ask. Neither side guarantees a fill, current availability or sufficient size. A midpoint, last trade, theoretical price or broker mark is a separately labeled valuation observation, not evidence that the short can be repurchased at bid. A complex order has its own net execution; individual quotes at different times do not establish that fill. [S48](../sources.md#s48) [S49](../sources.md#s49)

Wide or crossed markets can signal stale, fragmented or otherwise unsuitable data. Do not repair a suspicious quote by guessing a plausible price. Ask for a fresh compatible source or mark the exact trade unverified. Generic economics can remain useful if clearly labeled as assumptions.

## Slippage and scale

Transaction costs include commissions, exchange or regulatory charges where applicable, spread costs and market impact. An apparently small per-contract difference can matter when premium is small or management requires repeated transactions. The cost of closing and replacing a position belongs in a roll comparison; the opening premium alone is incomplete.

Liquidity is quantity-dependent. One displayed contract does not establish capacity for a much larger position. Illustrative one-contract economics help expose units but do not establish suitability. Larger quantities need corresponding funding, concentration and execution evidence. The approved sizing convention and owner limits govern what can be proposed.

A narrow spread today does not ensure a narrow spread during an earnings surprise or market stress. In a stress scenario, distinguish a mark used for analysis from actual realizable exit proceeds. If no reliable liquidation estimate exists, state the uncertainty rather than subtracting a token haircut and presenting it as a fact.

## Comparing contracts

Strikes and expirations can trade different kinds of risk. A higher premium may compensate for more directional exposure, an event, a longer commitment or worse liquidity. Compare economic terms and evidence together. A contract with a slightly lower premium can be preferable if it avoids an unwanted obligation or reduces management friction; that is a judgment, not a universal rule.

A contract metric belongs to its underlying/security, right, full expiry, strike, quote side, date and denominator. A shared ticker or strike does not make different records interchangeable. Spread percentages based on bid and midpoint are different measures; business research can carry across contracts while their quotes cannot. An aggregate median describes its contributing observations. Excluded missing, zero-sided or invalid quotes can leave a tight median with sparse coverage, and the listed-row count need not equal the valid-spread count. A one-observation median describes only that observation. [S54](../sources.md#s54) Do not assign an aggregate spread to a selected contract or treat it as evidence of that contract's liquidity without matching observations.
