# boligregner.dk Reference Data

## Current reference cases (21 cases, Apr 2023 + Oct 2026 captures)

All reference data is captured from boligregner.dk browser HTML files saved
in `tests/fixtures/`. The parser (`scripts/parse_boligregner.py`) reads saved
HTML only — boligregner.dk returns different data to `urllib` vs a real
browser.

### Single-component, 2.5M DKK provenu (Oct 2026)

| Case | Type | Rate | Price | Provenu | Maturity | Date |
|------|------|------|-------|---------|----------|------|
| f1_oct | f1 | 3.08% | 98.26 | 2,500,643 | 31y | 2026-10-10 |
| fixed_4pct | fixed | 4.00% | 93.84 | 2,500,154 | 30y | 2026-10-10 |
| f5_jan | f5 | 3.48% | 91.30 | 2,500,133 | 30y | 2026-10-10 |

### Single-component, 6.5M DKK provenu (Oct 2026)

| Case | Type | Rate | Price | Provenu | Maturity | Date |
|------|------|------|-------|---------|----------|------|
| f1_oct_6m | f1 | 3.08% | 98.26 | 6,500,770 | 31y | 2026-10-10 |
| fixed_1pct_6m | fixed | 4.43% | 64.03 | 6,500,818 | 30y | 2026-10-10 |
| cita_30_6m | cita | 2.69% | 100.13 | 6,500,495 | 30y | 2026-10-10 |

### Apr 2023 historical cases

| Case | Type | Rate | Price | Provenu | Maturity | Date |
|------|------|------|-------|---------|----------|------|
| apr2023_f1_20/25/30 | f1 | 3.73% | 99.03 | 2,658,833 | 20/25/30y | 2023-04-22 |
| apr2023_fast5_20–30 | fixed | 5.00% | 98.52–99.52 | 2,658,348–2,658,891 | 20–30y | 2023-04-22 |
| apr2023_tlaan_21–30 | t | 3.71–3.72% | 99.05 | 4,371,947 | 30y | 2023-04-24 |

## Correctness report

Run with:
```bash
uv run --extra dev python scripts/correctness_report.py
```

### Worst deviation by metric and loan type

| Metric | F1 | Fixed | F5 | CITA | T-lån |
|--------|-----|-------|-----|------|-------|
| Hovedstol | 0.00% | −0.07% | 0.00% | +0.05% | 0.00% |
| Ydelse | +1.35% | −0.07% | +0.36% | +0.04% | 0.00% |
| ÅOP | −2.89% | −0.46% | −1.10% | +0.46% | −10.32% |
| Hor. rente | −1.00% | +0.72% | +0.91% | +2.82% | +1.99% |
| Hor. afdrag | +2.83% | +0.04% | +1.45% | −0.60% | −1.02% |
| Hor. ydelse | +2.24% | +0.45% | +1.14% | +1.06% | +0.23% |
| Hor. restgæld | −0.62% | −0.09% | −0.16% | +0.13% | +0.22% |

### Key findings

- **Fixed loans: best fit.** All metrics ≤0.72%. Deep-discount (price 64.03)
  excellent: ydelse −0.02%, ÅOP −0.17%, restgæld −0.01%.
- **F1 loans: systematic ydelse overshoot** (+0.76–1.35% on apr2023, −0.05%
  on f1_oct). ÅOP consistently negative. Root cause: integer-only maturity
  rounding (F1 has 31y = 124 quarters from 360 months).
- **T-lån: ydelse exact, ÅOP broken.** Ydelse matches exactly (fixed input).
  ÅOP −9 to −10% — the IRR of `fixed_ydelse` cashflows against
  `net_disbursement` excludes bidrag from the cashflow stream.
