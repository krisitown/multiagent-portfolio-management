# 08 — Exercise, assignment, dividends and event risk

DRAFT knowledge. These mechanics explain obligations; they do not predict a particular holder's choice or a broker's account treatment.

## Exercise and assignment are different sides

A holder exercises a right; a writer receives an assignment under the clearing and broker allocation processes. The writer is not permanently paired with the investor who bought the opening trade. Standard US equity options are generally American-style, so an open short can be assigned before expiration. Assignment can involve some contracts and leave others open. OIC's assignment FAQ explains these distinctions. [S02](../sources.md#s02)

At expiry, exercise-by-exception procedures reduce administrative work for certain in-the-money options. OIC describes OCC's general one-cent threshold and the possibility of contrary instructions or exceptions. This does not guarantee that an in-the-money short will be assigned or that an out-of-the-money short will not be assigned. Customer deadlines and broker procedures must be verified separately. [S02](../sources.md#s02)

The expert should distinguish a likely scenario from an established event. “Assuming assignment” can be useful for funding analysis; “assignment occurred” requires evidence. Likewise, being comfortable owning the shares does not establish that sufficient funds will be available on the settlement date.

## Why early exercise can matter

Exercising an option can sacrifice remaining time value, but financing, dividends, borrow conditions and other considerations can make exercise relevant. A simplistic rule that holders never exercise before expiry is invalid. For short calls, an approaching ex-dividend date can increase assignment risk, particularly when remaining extrinsic value is small relative to the dividend benefit. This is a warning indicator, not a probability formula. [S02](../sources.md#s02)

Do not compare the dividend with the entire call premium. An in-the-money premium includes intrinsic value, which is different from the remaining time value. Even a correct comparison is incomplete without financing, exercise timing and holder circumstances. If expected dividend income is part of a covered-call thesis, explain how early assignment could change entitlement.

For puts, receiving strike cash earlier and changes in remaining time value can affect exercise incentives. Deep-in-the-money, low-extrinsic options deserve attention, but a deterministic assignment prediction is still inappropriate. A holder's operational constraints and instructions may differ from an abstract model's optimum.

## Expiration and after-hours exposure

Near expiry, a small move around the strike can materially change the likely shares and cash involved. After-hours information may arrive while exercise decisions are still possible under applicable procedures. A closing price therefore does not fully describe the writer's exposure to exercise choices. Tight option spreads before the close do not guarantee an orderly outcome after the market closes.

A protective long can expire unexercised while a short is assigned, depending on terms and instructions. The economic payoff relationship at one time does not ensure automatic coordinated exercise. Contract compatibility, funding, broker procedures and timing affect the operational outcome. Advisory comparison can include exercise and assignment alternatives; it does not authorize submitting instructions. Do not invent broker deadlines or claim an exercise, assignment or close has occurred without evidence.

Closing a short position can remove future assignment exposure for the closed contracts once effective, but an already processed assignment, incomplete fill or delayed record can complicate the account. Do not assert that a proposed close has happened or that a pending transaction guarantees a clean account. The current position source and broker procedures govern the factual claim.

## Dividends and corporate actions

Use declared dividends and source dates where available, distinguishing estimates from announcements. Ordinary and special distributions can have different contract implications. Splits, mergers, tender offers, spinoffs and other actions may alter the deliverable or lifecycle. Consult the applicable OCC memo rather than adapting a standard formula from a headline. [S33](../sources.md#s33)

Declaration, ex-dividend, record and payment dates are different facts. On the ex-date the share begins trading without the distribution entitlement; the theoretical price adjustment belongs to that date, not the payment date or the next session afterward. Other price moves can offset or amplify it. All else equal, a lower underlying raises a long put's value and the corresponding short put's liability; it is adverse to the short put, not a mechanical seller benefit. Expected dividends already affect option pricing, so the expected adjustment is not a free trading profit or a guaranteed one-day option loss. Count a dividend receipt only for entitled shares and distinguish special-distribution contract adjustments. [S53](../sources.md#s53)

## Earnings and other discontinuities

Earnings, regulatory rulings, litigation, product decisions, refinancing announcements and transactions can create discontinuous repricing. An option that expires after an event carries different exposure from one that expires before it. Event calendars can change, and confirmation matters when the date is decisive.

Premium around an event is compensation for taking a contingent exposure, not a free addition to ordinary yield. An attractive annualized number can conceal a single large jump risk. The investor may rationally accept, reduce, defer or reject that exposure depending on evidence and capacity. The expert must not automatically ban events or automatically favor their higher premiums.

For a company under trading halt, a lack of executable quotes need not eliminate option obligations. A stale position mark cannot establish that risk is unchanged. The reported holding remains account evidence despite missing public quotes. Do not silently remove it or treat unknown event or assignment facts as verified. Any additional public research depends on the exposed host capability and review-kind authority; it does not reenter the Lead conversation. Limited data can justify an unverified conclusion while still supporting an urgent funding warning.
