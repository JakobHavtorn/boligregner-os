# Danish Mortgage Concepts Guide

A distilled reference for the concepts the calculator models. All monetary examples are illustrative.

## Choosing a mortgage

No single loan is universally best. The loan with the lowest initial payment may be expensive to redeem; the one that performs best under rising rates may be worst when rates fall. The decision requires a full "ingredient list" — total cost over a horizon, redemption value, and equity — not just the advertised monthly payment.

The key analytical tool is the **horizon calculation**: total loan costs over a five-year period under three rate scenarios — unchanged, rising, and falling. This is called the **period cost** (periodeomkostning).

## Period cost

Period cost = total payments after tax over five years + redemption amount after five years − original loan proceeds.

Example: a 2,000,000 kr loan. Over five years you pay 360,000 kr in principal, interest, and administration. The remaining debt can be redeemed for 1,800,000 kr.

> Period cost = 1,800,000 + 360,000 − 2,000,000 = **160,000 kr**.

The loan with the lowest start payment is not always the cheapest. Amortization (principal repayment) is savings, not a cost — it reduces the remaining debt and therefore future interest and administration charges.

### The three-way comparison

The source example compares three loans on 2,000,000 kr: a fixed-rate with amortization (7,926 kr/mo after tax), a variable F1 with amortization (6,624 kr/mo), and a variable F1 interest-only (1,494 kr/mo). The interest-only loan has the lowest start payment — 6,432 kr/month less than the fixed-rate loan — but it is not always the cheapest over five years.

Key principles from the source material:
- Amortization is savings, not a cost — it directly reduces remaining debt and therefore future interest and administration charges.
- When rates fall, all loans redeem at par (100). The loan with the highest principal costs the most to redeem.
- When rates rise, fixed-rate bonds can be redeemed below par, yielding a gain. Variable-rate loans redeem near par (100).
- Interest-only loans carry a higher administration rate (bidragssats) and get no reduction in interest, administration, or debt.

The interaction between rate path, loan type, and redemption price is explored in detail on the kurs 95, conversion, and refinancing pages below.

## Loan types

The calculator covers these loan types for private homeowners:

- **Fixed-rate bonds and cash loans** (fastforrentede obligations- og kontantlån)
- **Variable-rate loans**: F1, F3, F5, F10 (rentetilpasningslån)
- **CITA loans** (FlexKort®) — short-period variable rate
- **T loans** — variable-rate loan with fixed payment and variable maturity
- **Guarantee loans** — variable rate with a rate cap (garantilån)
- **Priority loans** — bank loans with the same security as mortgage loans (prioritetslån)

## F1 with and without amortization, and the T loan

Three 30-year variable-rate loans, each with 1,000,000 kr proceeds, financed by issuing the same 1-year bonds:

| Loan | Coupon | Principal (kr) | ÅOP pre-tax | Payment/mo after tax (kr) |
|------|--------|----------------|-------------|---------------------------|
| F1, interest-only | 1-yr | 1,024,000 | 1.54% | 1,332 |
| F1, with amortization | 1-yr | 1,024,000 | 1.44% | 3,530 |
| T (as 5% fixed) | 1-yr | 1,024,000 | 1.70% | 5,549 |

### Period cost findings

The T loan has the lowest period cost; the interest-only F1 has the highest. This holds whether rates rise or fall.

The difference in monthly payment between the three loans can be 200,000–310,000 kr depending on the rate level. Yet period costs differ by at most ~14,000 kr. This is because period cost measures payment + redemption amount minus original proceeds. The payment difference is mainly amortization, and amortization produces a correspondingly lower remaining debt at period end.

**Why the T loan is cheapest**: higher amortization means less interest and administration paid. The interest-only loan gets no reduction in interest, administration, or debt.

**Why the T loan has the highest ÅOP**: All three loans have the same fixed cost (23,797 kr = 2.38% of proceeds). ÅOP spreads fixed costs over the loan's life proportional to remaining debt. The T loan is the shortest (16.4 years at unchanged rate), so its annualized cost percentage is highest. The interest-only loan runs 30 years, diluting the fixed cost — but its actual 5-year cost is highest.

### T loan specifics

The T loan sets the pre-tax payment equal to a 30-year 5% fixed-rate loan. At unchanged rates, the term is 16.4 years. If rates rise, the term extends (to ~20 years at the example's rate shock); if rates fall, it shortens (~15.9 years). Only if the F1 rate exceeds 5% must the payment increase, to respect the 30-year maximum term.

The T loan's constant pre-tax payment is a liquidity feature, not rate protection. Period costs still rise with rates — the borrower pays the full rate increase, but the institute smooths the cash-flow impact by extending amortization.

A 16-year F1 loan achieves nearly the same period cost as the T loan. The T loan's advantage is budgetary predictability, not economics.

### Redemption and equity

After five years:
- Interest-only borrower: still owes ~1,000,000 kr + establishment costs.
- F1 with amortization: owes 858,714–902,264 kr.
- T loan: owes 704,287–809,886 kr.

Lower redemption amount means more equity realized on sale and less risk of technical insolvency (debt exceeding property value).

## Equity (friværdi)

Equity = property value − remaining debt after five years.

Example: property purchased for 2,000,000 kr with 100,000 kr down. Loan need: 1,900,000 kr. Three alternatives: F3 with amortization, F3 interest-only, and fixed-rate.

Key findings:
- Equity is strongly dependent on loan choice.
- Equity rises and falls with the sale price, but the **difference in equity between loans is independent of the sale price**.
- Amortization builds equity. At unchanged rates, F3 with amortization has 240,000 kr more equity than the interest-only F3.
- Interest-only: borrow 1,900,000 kr, owe 1,953,000 kr after five years. The 53,000 kr increase comes from establishment and redemption costs.
- Under rising rates, the fixed-rate loan's redemption price falls, giving it the highest equity. Fixed-rate loans have built-in equity protection when rates rise.

**Equity as percentage of purchase price** is a key metric. If equity is 280,000 kr on a 2,000,000 kr property, that is 14%. Property values can fall 14% before debt exceeds the property's value.

**Why positive equity matters**:
- Selling converts positive equity to cash surplus.
- Negative equity at sale must be covered by savings or unsecured loans (with worse terms).
- Houses need maintenance. Equity enables future financing for repairs.

## The kurs 95 rule

Banks and mortgage institutes advise against taking fixed-rate loans at prices (kurs) below 95. For tax reasons, loans may not be issued above kurs 100. This limits the available fixed-rate menu.

Example (3 December 2010), 30-year loans, 1,000,000 kr desired proceeds:

| Bond | Kurs | Principal (kr) | ÅOP after tax | Payment/mo after tax (kr) |
|------|------|----------------|---------------|---------------------------|
| 4%, interest-only | 92.95 | 1,103,000 | 3.75% | 2,808 |
| 4%, with amortization | 94.47 | 1,085,000 | 3.74% | 4,334 |
| 5%, interest-only | 99.91 | 1,025,000 | 3.96% | 3,187 |

The two 4% loans have nearly the same ÅOP; the 5% loan is slightly more expensive. Yet the recommendation is the 5% loan.

### Why: the 4% bond is worst when rates fall, best when rates rise

Since payments are fixed on all three loans, the difference lies in how the redemption amount changes with rates.

- **Rates fall**: All loans redeem at 100. The 4% interest-only bond has a higher principal, so its redemption is ~75,000 kr more than the 5% bond. After offsetting the payment gain, the 4% interest-only period cost is ~54,438 kr higher than the 5% interest-only. The 4% with amortization sits between.

- **Rates unchanged**: The 4% interest-only bond redeems at 95.56; the 5% at 100. The 5%'s lower principal makes it cheaper to redeem, compensating for its higher payment. The 4% with amortization has the lowest period cost.

- **Rates rise**: All three redeem below 100. Interest-only bonds are more price-sensitive than amortizing bonds, and 4% bonds fall more than 5% bonds. The 4% interest-only has the lowest period cost; the 5% has the highest.

### Is 95 magical?

No. There is nothing special about the number 95. Whether you borrow at 99.9, 95, or 92 should depend on a concrete trade-off between payment differences and redemption conditions.

The rule exists to protect borrowers from rising debt if rates fall. But the maximum redemption price is 100 — the risk has a ceiling. The lower-coupon bond typically has the lower payment and better redemption terms if rates rise.

Borrowing near kurs 100 means a higher payment in exchange for better position if rates fall. The question is how large a premium to pay for that option. Rate falls are normally associated with rising house prices, so the risk is one most homeowners can afford.

## Converting fixed-rate loans (konvertering)

Conversion replaces an existing fixed-rate bond loan with a new one at a different coupon. The decision depends on the new loan's price relative to par.

### Example: converting a 6% loan

Existing: 27-year 6% interest-only, 1,000,000 kr remaining, redeeming at 100.

| Loan | Kurs | Principal (kr) | ÅOP after tax | Payment/mo after tax (kr) |
|------|------|----------------|---------------|---------------------------|
| Existing 27-yr 6% IO | 100 | 1,000,000 | 4.18% | 3,652 |
| 30-yr 5% IO (new) | 99.91 | 1,039,000 | 3.84% | 3,220 |
| 30-yr 4% w/ amort. (new) | 94.47 | 1,100,000 | 3.62% | 4,394 |
| 30-yr 4% IO (new) | 92.95 | 1,118,000 | 3.66% | 2,858 |

Period cost for the existing loan = payments over the next five years + redemption in five years − redemption today (after tax). This is the extra cost of keeping the old loan rather than redeeming it for cash.

### Converting 6 → 5% is straightforward

The 5% bond trades near 100, so conversion gives roughly the same remaining debt minus redemption costs, plus a lower payment. It is advantageous in all three rate scenarios: slightly cheaper at unchanged or falling rates (lower payment), and substantially cheaper at rising rates (the 5% bond falls more in price).

### Converting 6 → 4% increases remaining debt

The 4% bond trades below 95, so conversion produces a higher principal. The payment is lower and the gain from rising rates is larger. But if rates fall, you are left with a higher debt to redeem at 100 — keeping the 6% loan would have been better.

### Two-coupon rule

A Danish rule of thumb: only convert when the coupon difference is at least 2 percentage points (e.g., 6 → 4%, not 6 → 5%), and wait until the new bond is near kurs 100 to avoid rising debt.

Analysis shows converting 6 → 5% near kurs 100 costs nothing: if rates later fall another point, you convert again. Conversion costs are relatively small because the stamp duty on the existing loan can be offset.

### When to convert

- New loan near kurs 100: converting down in coupon is almost always advantageous.
- New loan well below kurs 100: a trade-off — higher debt if rates fall, but better position if rates rise.
- If you expect rates to rise, convert before the opportunity passes.

### Conversion costs

The calculation accounts for all costs after tax: redeeming the old loan (difference interest and fees) and establishing the new one (fees, registration duty). The new loan's proceeds must cover these costs.

The new loan often carries a higher administration rate (bidragssats). Since the rate is individual, always enter the rate you are actually offered.

## Refinancing (omlægning)

Refinancing replaces an existing loan with new alternatives. The period cost comparison shows whether switching is worthwhile.

### Example: 27-year 5% fixed with amortization → three alternatives

| Existing | Keep | F5 | 3.5% fixed (kurs 99.32) | 3% fixed (kurs 95.47) |
|----------|------|----|------------------------|----------------------|
| Period cost (kr, after tax) | ~340,000 | 140,000–175,000 | Lower payment, small debt increase | Lowest payment, largest debt increase |

- **Keep existing**: ~340,000 kr period cost regardless of rate movement.
- **F5**: substantial saving across all three rate scenarios. Rate stays roughly fixed for the five-year period.
- **3.5% fixed (kurs 99.32)**: lower payment; debt rises only slightly because price is near 100. Loss if rates fall (higher debt redeemed at 100), but manageable. Substantial saving if rates rise (redeem below par).
- **3% fixed (kurs 95.47)**: even higher debt and larger loss if rates fall. Most advantageous if rates rise. Significant risk in a rate-fall scenario.

Conclusion: it is generally favorable to leave the old loan. F5 gives savings across all scenarios. The 3.5% fixed is particularly attractive if rates subsequently rise. The 3% fixed carries substantial risk if rates fall.

## Comparing loan offers

### Loan types covered

- Fixed-rate bonds and cash loans
- Variable-rate F1, F3, F5, F10
- CITA (FlexKort®)
- T loans (fixed payment, variable maturity)
- Guarantee loans (variable rate with cap)
- Priority loans (bank loans with mortgage-grade security)

### Horizon calculations

Period costs are computed for different loan types side by side, under unchanged, rising, and falling rate scenarios.

### Two-layer financing (tolagsbelåning)

Introduced in 2013: up to 60% of property value (bottom loan) financed with SDO bonds; 60–80% (top loan) with RO bonds. The two-layer structure was withdrawn in 2014, but splitting a loan into multiple products remains possible. RO bonds are no longer used.

### Administration rate (bidragssats)

Administration rates depend on the loan-to-value bracket, with surcharges for variable-rate and interest-only loans. They vary across institutes and customers. The calculator uses standard rates for fixed, variable, and variable-with-interest-only, but in practice always enter the rate you are offered.

### Rate-cut fee (kursskæring ved refinansiering)

For variable-rate loans, a fee applied at each refinancing. Unlike the administration rate, it does not appear in the start payment but affects total cost over the period. The calculator uses 0.2% as the standard rate-cut. To compensate for a different rate, divide the rate-cut by the number of years between refinancings and add the result to the administration rate. Example: 0.2% rate-cut = 0.2% hidden administration increase for F1 (annual), 0.1% for F2 (biennial), etc.

### Bank loan rate

When property value and loan-to-value limits are included, the calculator allocates up to 80% of property value to mortgage loans and covers the remainder with a bank loan. The bank loan rate varies by customer; enter the rate you are offered. The rate is assumed to rise and fall with the general rate level in horizon calculations.

Priority loans are bank loans offered within the 80% lending interval as an alternative to mortgage loans. The offered rate can likewise be entered.

### Daily updated prices

Bond prices update daily based on the previous day's official prices. For loan types offered by multiple institutes, an average price is used for the underlying bonds.
