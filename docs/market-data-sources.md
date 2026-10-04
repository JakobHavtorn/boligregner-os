# Market Data Sources for boligregner-os

Research conducted 2026-10-04. All API calls verified live.

## Summary

The calculator currently hardcodes interest rates, bidragssatser, bond prices, issue costs, and the
bank rate in `engine.py` PRESETS. This document identifies open-access sources that can replace
those hardcoded values, organized by what data they provide and how to access them.

**TL;DR — five sources cover all needs:**

| Source | What you get | Access | Cadence |
|--------|-------------|--------|---------|
| DST Statbank API | Effective realkredit rates, avg bidragssats, ÅOP by fixation period (F3/F5/FIXED) | JSON/CSV POST, no key | Monthly |
| ECB Data Portal API | Bank loan rate (banklån) | SDMX REST CSV, no key | Monthly |
| Mybanker.dk | Per-LTV × per-loan-type × per-afdragsfrihed bidragssatser, all 4 institutes | HTML tables, plain curl | Ad-hoc |
| RD.dk renteudvikling | Nominal flexlån rates (F1–F10), med/uden afdrag | HTML tables, plain curl | Semi-annual |
| Finans Danmark XLSX | Weekly obligationsrenter (proxy for fixed-rate coupon) | XLSX (URL discovery) | Weekly |

---

## Tier 1 — DST Statbank (clean JSON/CSV API, no key)

**Endpoint:** `POST https://api.statbank.dk/v1/data/{table}`

JSON body with `format` at top level. Variable codes uppercase (`DATA`, `INDSEK`, `VALUTA`,
`RENTFIX`, `TID`). Use `(1)` for most recent period, `(-3)` for last 3. Eliminable variables
(`elimination=true`) can use `*`.

License: CC 4.0 BY.

### Table DNRNURI — new mortgage loans (monthly)

Best table for per-loan-type rates. Has a `RENTFIX` dimension that maps to F3/F5/FIXED.

**RENTFIX → LoanType mapping:**

| RENTFIX code | Label | Maps to |
|---|---|---|
| `3A` | Over 2 år og op til og med 3 år | F3 |
| `5A` | Over 3 år og op til og med 5 år | F5 |
| `S10A` | Over 10 år | FIXED (>10yr obligation) |
| `1A` | Over 6 mdr. og op til og med 1 år | F1 — **no data** (`..`) |
| `10A` | Over 5 år og op til og med 10 år | 5-10yr fixed (not directly used) |
| `ALLE` | All periods | Aggregate — do not use as per-type |

**DATA dimension values:**

| Code | Meaning |
|---|---|
| `AL51EFFR` | Effektiv rentesats inkl. bidrag (pct.) — annualized effective rate |
| `AL51BIDS` | Bidragssats (pct.) — average across all LTV brackets |
| `AL50AAOP` | ÅOP (pct.) |
| `AL50FORO` | Forretningsomfang (mio. kr.) |

**Live data (Aug 2026, households=1400, DKK):**

| RENTFIX | Eff. rente incl. bidrag | Bidragssats | ÅOP |
|---|---|---|---|
| 3A (F3) | 3.899% | 0.983% | 4.024% |
| 5A (F5) | 3.829% | 0.771% | 3.954% |
| S10A (FIXED) | 4.818% | 0.658% | 5.110% |
| 1A (F1) | `..` | `..` | `..` |
| ALLE | 4.091% | 0.738% | 4.273% |

**Critical limitations:**

1. **No LTV-bracket dimension.** Bidragssatser are averages across all LTV bands, not per-bracket.
   For per-LTV bidragssatser, use Mybanker.dk (Tier 3).
2. **F1 has no data.** The `1A` bucket is empty in DNRNURI. F1 rates need a different source.
3. **"Effektiv rentesats inkl. bidrag" ≠ nominal rate + bidragssats.** It is an annualized
   effective rate accounting for compounding and payment frequency. You **cannot** derive the
   nominal `LoanSpec.rate` field by subtracting bidrag from the effective rate. DST gives
   effective rate + ÅOP for validation/comparison, not for directly populating `rate`.
4. **No nominal coupon rate.** Only the effective rate. For nominal flexlån rates, scrape
   RD.dk renteudvikling (see Tier 3c). For fixed-rate proxy, use Finans Danmark obligationsrente.

**Example API call (CSV — simplest to parse):**

```json
{
  "table": "DNRNURI",
  "format": "CSV",
  "variables": [
    {"code": "DATA", "values": ["AL51EFFR", "AL51BIDS", "AL50AAOP"]},
    {"code": "INDSEK", "values": ["1400"]},
    {"code": "VALUTA", "values": ["DKK"]},
    {"code": "RENTFIX", "values": ["3A", "5A", "S10A"]},
    {"code": "TID", "values": ["(1)"]}
  ]
}
```

### Table DNRUURI — outstanding mortgage loans (monthly)

Gives average bidragssats and effective rate across all outstanding realkredit loans. Less
granular than DNRNURI (no RENTFIX dimension). Useful for tracking the aggregate bidragssats
trend over time.

**Live data (Aug 2026, households=1400, DKK):**

| Month | Eff. rente incl. bidrag | Bidragssats |
|---|---|---|
| 2026M06 | 3.412% | 0.804% |
| 2026M07 | 3.447% | 0.767% |
| 2026M08 | 3.453% | 0.767% |

### Table DNRENTD — daily official rates and money market rates

Published by Danmarks Nationalbank, republished via DST. Contains official interest rates,
swap rates, bond yields. Accessible via same DST API.

---

## Tier 2 — ECB Data Portal (SDMX REST, no key)

**Endpoint:** `GET https://data-api.ecb.europa.eu/service/data/MIR/M.DK.B.A2C.A.R.A.2250.DKK.N`

Query params: `lastNObservations=1&format=csvdata`

No API key. Returns CSV (one row with `OBS_VALUE`) or JSON (SDMX 2.1 structure with nested
`dataSets[0].series`). CSV is simpler.

**Live data:** 4.03% (Aug 2026) — bank interest rate for house purchase, Denmark, new business,
annualised agreed rate (AAR).

**What it gives you:** the banklån rate — the rate on the bank component of two-component
alternatives. Currently hardcoded at 4.5% across all presets; actual market rate is 4.03%.

---

## Tier 3 — Mybanker.dk (server-rendered HTML, no Cloudflare)

**URL:** `https://www.mybanker.dk/sammenlign/bolig/bidragssatser`

278KB static HTML, 5 tables, no JS rendering, no Cloudflare. Scrapable with plain `curl`.
Cache TTL: 24 hours is reasonable — bidragssatser change infrequently (months, not days).

**This is the primary source for per-LTV-bracket bidragssatser.** It aggregates all four
Danish realkredit institutes in a single page with absolute rates (not deltas).

### Table 0 — Overview (all institutes, 0-80% blended rates)

Blended weighted-average rates for 0-80% LTV. Useful for quick comparison, but **not per-bracket**
— use tables 1-4 for per-LTV values.

| Institute | Fast (med afdrag) | Fast (uden afdrag) | F1 (med) | F1 (uden) |
|---|---|---|---|---|
| Jyske Realkredit | 0.5625% | 0.7175% | 0.7500% | 0.7500% |
| Nykredit/Totalkredit | 0.7375% | 0.9050% | 0.9000% | 1.1750% |
| Nordea Kredit | 0.5375% | 0.7125% | 0.7500% | 1.0500% |
| Realkredit Danmark | 0.5105% | 0.7105% | 0.8063% | 0.9725% |

### Tables 1-4 — Per-institute, per-LTV-bracket detail

LTV brackets: 0-40%, 40-60%, Over 60%.

**Table 1: Jyske Realkredit**

| LTV | Fast med | Fast uden | F1 med | F1 uden | F5-F6 med | F5-F6 uden |
|---|---|---|---|---|---|---|
| 0-40% | 0.2250% | 0.2250% | 0.3750% | 0.5250% | 0.4250% | 0.4250% |
| 40-60% | 0.8000% | 0.8000% | 0.9500% | 1.1000% | 1.0000% | 1.0000% |
| Over 60% | 1.0000% | 1.6200% | 1.3000% | 1.9200% | 1.2500% | 1.8700% |

Also has F2-F4 and Jyske Frihed columns. Cross-referenced with Jyske Bank's own page —
identical values.

**Table 2: Nykredit/Totalkredit**

| LTV | Fast med | Fast uden | F1-F2 med | F1-F2 uden | F3-F4 med | F3-F4 uden | F5-F10 med | F5-F10 uden |
|---|---|---|---|---|---|---|---|---|
| 0-40% | 0.4500% | 0.4500% | 0.7500% | 0.8500% | 0.7000% | 0.8000% | 0.5000% | 0.6000% |
| 40-60% | 0.8500% | 0.8700% | 1.3000% | 1.4500% | 1.2500% | 1.4000% | 1.0500% | 1.2000% |
| Over 60% | 1.2000% | 1.8500% | 1.9000% | 2.5500% | 1.6500% | 2.3000% | 1.4500% | 2.1000% |

**Table 3: Nordea Kredit**

| LTV | Fast med | Fast uden | F1 med | F1 uden | F3 med | F3 uden | F5&Kort med | F5&Kort uden |
|---|---|---|---|---|---|---|---|---|
| 0-40% | 0.2250% | 0.2250% | 0.6500% | 0.6700% | 0.6000% | 0.6200% | 0.4000% | 0.4200% |
| 40-60% | 0.6750% | 0.7250% | 1.2500% | 1.3250% | 1.1250% | 1.2000% | 0.9250% | 1.0000% |
| Over 60% | 1.0250% | 1.6750% | 1.6500% | 2.3000% | 1.4750% | 2.1250% | 1.2750% | 1.9250% |

**Table 4: Realkredit Danmark**

| LTV | Fast med | Fast uden | F1-F2 med | F1-F2 uden | F3-F4 med | F3-F4 uden | F5 med | F5 uden |
|---|---|---|---|---|---|---|---|---|
| 0-40% | 0.2060% | 0.2060% | 0.6300% | 0.6300% | 0.5800% | 0.5800% | 0.3800% | 0.3800% |
| 40-60% | 0.6180% | 0.6180% | 1.0900% | 1.0900% | 1.0400% | 1.0400% | 0.8400% | 0.8400% |
| Over 60% | 1.0120% | 1.8120% | 1.5400% | 2.3400% | 1.4900% | 2.2900% | 1.2900% | 2.0900% |

### Loan-type normalization

Each institute uses different groupings. The project's `LoanType` enum has `F1`, `F3`, `F5`,
`FIXED`, `T`. A normalization layer is needed:

| Institute | Product name | Maps to LoanType |
|---|---|---|
| Jyske | Jyske Fast Rente | FIXED |
| Jyske | Jyske Rentetilpasning F1 | F1 |
| Jyske | Jyske Rentetilpasning F2-F4 | F3 (closest match) |
| Jyske | Jyske Rentetilpasning F5-F6 | F5 |
| Nykredit | Fastforrentet lån | FIXED |
| Nykredit | Flekslån F1-F2 | F1 |
| Nykredit | Flekslån F3-F4 | F3 |
| Nykredit | Flekslån F5-F10 | F5 |
| Nordea | Fastforrentet lån | FIXED |
| Nordea | Flekslån F1 | F1 |
| Nordea | Flekslån F3 | F3 |
| Nordea | Flekslån F5 & Kort Rente | F5 |
| RD | Fastforrentet lån | FIXED |
| RD | Flekslån F1-F2 | F1 |
| RD | Flekslån F3-F4 | F3 |
| RD | Flekslån F5 | F5 |

The "0-60%" and "0-80%" columns on Jyske's own page are blended weighted averages, not
separate brackets. The per-band brackets are 0-40%, 40-60%, Over 60%. Do not store blended
columns as bracket values.

### Considerations for open-source use

The underlying bidragssatser are public data published by each institute. Mybanker.dk's
value-add is the aggregation and comparison layout. Scraping their page for an open-source
calculator is reasonable — the data is public, the page is not behind authentication, and
there is no API to license. Cache for 24 hours to minimize requests.

---

## Tier 3b — Finans Danmark XLSX (weekly obligationsrenter)

**URL:** Scrape `https://finansdanmark.dk/tal-og-data/boligstatistik/obligationsrenter` to
discover the current XLSX link. The URL embeds the week number and a media hash that may
rotate: e.g. `/media/ahijw5sn/obl-rente-w39-26.xlsx`. A hardcoded URL will 404 within a week.

**Data (week 39, 2026):**

| Series | Value | Meaning |
|---|---|---|
| Kort rente (DKK) | 2.91% | Short realkredit bond yield — relevant to F1 flexlån |
| Lang rente (DKK) | 4.49% | Long realkredit bond yield — proxy for fixed-rate coupon |
| Kort euro rente | 2.82% | Short realkredit bond yield in EUR |

These are **bond market yields** (what investors demand), not borrower rates. The realkredit
institute uses them to set coupons on new obligations. The lang obligationsrente is a
reasonable proxy for the fixed-rate coupon, but not identical to the borrower's nominal rate
(which also depends on issue price/kurs).

Historical note: the XLSX contains data from 1997. The 7.46% "Lang rente" seen in early
rows is from 2022 (post-rate-hike era), not current.

---

## Tier 3c — Nominal realkredit rates (flexlån)

**The nominal rate IS available — my earlier report was wrong.**

Three sources publish the actual nominal (kupon) rate for flexlån, not just the effective rate:

### RD.dk — FlexLån® K renteudvikling ✅ Works (server-rendered HTML)

**URL:** `https://rd.dk/laantyper/flexlaan-k/renteudvikling`

295KB, 4 static HTML tables, no Cloudflare. Scrapable with plain `curl`.

Tables 0-1 show **historical kontantlånsrenter** (nominal rates) for F1 through F10, both
med/uden afdrag, going back to 2006. Updated semi-annually (January and April, after each
rate-setting auction).

**Live data (Apr 2026, Realkredit Danmark):**

| Ref. dato | F1 | F2 | F3 | F4 | F5 | F10 |
|---|---|---|---|---|---|---|
| apr-26 | 2,27% | 2,35% | 2,39% | 2,53% | 2,66% | 3,19% |
| jan-26 | 2,29% | 2,34% | 2,34% | 2,45% | 2,57% | 3,10% |

These are the **actual nominal rates** that go into `LoanSpec.rate` — the rate borrowers pay,
after deduction of kursfradrag, before bidragssats. This is exactly what the presets need.

Tables 2-3 show EUR rates (less complete history).

**Limitations:**
- Only Realkredit Danmark rates — other institutes may differ slightly.
- Updated semi-annually, not daily. The current rate for a new loan may differ from the
  table if market rates have moved since the last auction.
- No per-LTV-bracket breakdown (the rate is the same regardless of LTV; only bidragssats
  varies by LTV).

### Nordea — rate forecast pages ✅ Works (server-rendered HTML)

**URL:** `https://www.nordea.com/da/nyhed/forventninger-til-f1-f3-og-f5-renten-1.-oktober-2026`

Static HTML, no Cloudflare. Published before each auction (quarterly).

Shows both **current rates** and **forecast rates**:

| Loan type | Current rate (Jun 2026) | Forecast (Oct 2026 auction) |
|---|---|---|
| F1 | 2,17% | 2,90% |
| F3 | 3,66% | 2,90% |
| F5 | -0,10% | 3,00% |

Note: "Renterne er oplyst efter fradrag for kursfradrag" — these are the rates after
deducting the kursfradrag, which is what the borrower actually pays. This matches what
goes into `LoanSpec.rate`.

The F5 rate of -0,10% is from the October 2021 auction (5 years ago), still in effect
until October 2026. The forecast of 3,00% is what it will reset to.

**Limitations:**
- Nordea Kredit does not issue F1 loans.
- This is a news article, not a data page — the URL changes with each auction.
- Only shows the next auction's forecast, not historical data.

### Jyske Bank — referencerenter ✅ Works

**URL:** `https://www.jyskebank.dk/bolig/boliglaan/referencerenter`

Returns current reference rates (CIBOR 3M, CIBOR 6M, CITA 3M) for variable-rate loans.
Content-negotiation returns markdown, making it trivially parseable.

**Live data:**

| Product | Reference | Rate p.a. | Valid |
|---|---|---|---|
| Jyske Kort Rente | CIBOR 3M | 2,2833% | Jul–Sep 2026 |
| Prioritetslån | CIBOR 3M | 2,2700% | Jul–Sep 2026 |
| Boligkredit Real | CIBOR 3M | 2,290% | Jul–Sep 2026 |

**Limitations:**
- Only reference rates (CIBOR/CITA), not the final borrower rate. The borrower rate
  = reference rate + spread, where the spread is set by the institute.
- Does not include F3/F5 rates (those are set at auction, not referenced to CIBOR).

### Summary: nominal rate sourcing

| Loan type | Best source | What you get | Cadence |
|---|---|---|---|
| F1 | Jyske Bank referencerenter (CIBOR 3M + spread) or RD.dk renteudvikling | Nominal rate | Daily (CIBOR) / semi-annual (RD) |
| F3 | RD.dk renteudvikling | Nominal rate (post-kursfradrag) | Semi-annual |
| F5 | RD.dk renteudvikling | Nominal rate (post-kursfradrag) | Semi-annual |
| FIXED | Finans Danmark lang obligationsrente (proxy) or RD.dk obligationslån page | Bond yield (proxy for coupon) | Weekly / daily |

The nominal rate for flexlån is the **kontantlånsrente** — the rate after deducting the
kursfradrag from the bond yield. It's what borrowers actually pay, and it's what goes
into `LoanSpec.rate`. This is distinct from:
- The **obligationsrente** (bond yield) — what investors receive
- The **effektive rente inkl. bidrag** (DST) — annualized effective rate including bidrag

For fixed-rate loans, the nominal rate is the coupon rate, which equals the bond yield
at issuance. The Finans Danmark lang obligationsrente (4.49%) is a reasonable proxy.

---

## Sources that don't work

| Source | Issue |
|---|---|
| Nordea (`nordea.dk/.../hvad-er-bidragssats.html`) | Tables show **delta values** (basis-point adjustments), not absolute bidragssatser. Not usable for bidragssatser. |
| Realkredit Danmark (`rd.dk/privat/priser`) | JS-rendered SPA for bidragssats page. However, `rd.dk/laantyper/flexlaan-k/renteudvikling` IS scrapable for nominal rates. |
| Nasdaq Copenhagen | Bond prices. No free API. Requires data agreement. |

---
---

## What's hardcoded that could be sourced

| Value | Code location | Current hardcoded | Best source | Per-LTV? |
|---|---|---|---|---|
| Bidragssats default | `engine.py:55` | 0.006 (0.6%) | Mybanker.dk (all 4 institutes) | ✅ Yes |
| F3 realkredit rate | `engine.py:105` | 0.035 (3.5%) | RD.dk renteudvikling (2,39% Apr 2026) | No |
| F5 realkredit rate | `engine.py:115` | 0.042 (4.2%) | RD.dk renteudvikling (2,66% Apr 2026) | No |
| FIXED realkredit rate | `engine.py:125` | 0.04 (4.0%) | Finans Danmark lang obligationsrente (4.49%) — proxy | No |
| FIXED bond price | `engine.py:126` | 94.52 | No free API — Nasdaq requires data agreement | No |
| Bank rate | `engine.py:107,117,127,137,148` | 0.045 (4.5%) | ECB MIR API (4.03%) | No |
| Issue costs | `engine.py:109,119,129,139,150` | 0.0171–0.0182 | No API — institute websites only | No |

## What cannot be sourced from any free API

- **Bond prices** (obligation kurser): No free Danish API. Nasdaq Copenhagen requires a data
  agreement. Institute pages show some prices but are JS-rendered.
- **Issue costs** (udstedelsesomkostninger): Set by individual institutes, published on
  websites, no structured API.

## What should stay as user input

| Value | Code location | Current default | Reason |
|---|---|---|---|
| Tax rate | `engine.py:99`, `models.py:150` | 0.336 (33.6%) | User-specific, not market data |
| Bank share | `engine.py:108` | 0.20 (80/20 split) | User choice, not market data |
| Rate shocks | `models.py:154` | [-2%, 0, +2%] | Analysis parameter, not market data |
| T-lån fixed_ydelse | `engine.py:140` | 9900 | Loan-specific, not market data |
| Maturity years | `engine.py:68` | 30 | Loan choice, not market data |

---

## Modeling gap

Current `LoanSpec.bidragssats` is a flat `Decimal`. Real Danish bidrag varies by
`(institute, loan_type, LTV_band, afdragsfrihed)`. Mybanker.dk gives 3 LTV brackets
(0-40%, 40-60%, Over 60%) × ~4-5 loan types × 2 afdragsfrihed variants per institute.

A lookup layer is needed:

```
bidragssats = lookup(institute, loan_type, ltv_band, afdragsfrihed) → Decimal
```

This keeps the engine interface unchanged — the lookup populates the existing flat
`bidragssats` field before `calculate()` is called.

The "blended" 0-60% and 0-80% rates on Jyske's page are computed by weighting each band's
share of the loan, as the footnote describes: "beregnes ved at fordele lånets kontantværdi
i forhold til ejendommens værdi i belåningsintervaller." Use the three per-band rates
(0-40%, 40-60%, Over 60%) and compute blended rates at the application layer if needed.

---

## Recommended new API endpoints

```
GET /api/market-rates              → current rates by loan type + fixation period
GET /api/bidragssatser             → bidragssatser by institute + LTV bracket + loan type
GET /api/market-rates/refresh      → force cache refresh (admin)
```

MCP tool: `get_market_rates` returns current sourced rates for auto-populating calculator
inputs.

## Caching strategy

| Source | TTL | Rationale |
|---|---|---|
| DST Statbank | 24h | Monthly data, updated 1st of month |
| ECB Data Portal | 24h | Monthly data |
| Mybanker.dk | 24h | Bidragssatser change infrequently (months) |
| RD.dk renteudvikling | 24h | Semi-annual data, updated Jan/Apr after auctions |
| Finans Danmark XLSX | 24h | Weekly data, URL changes weekly |

Store cached data as JSON in `data/market-rates.json`. Fall back to last-good value if a
source is unavailable.
