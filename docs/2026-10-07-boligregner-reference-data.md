# boligregner.dk Reference Data (captured 2026-10-06)

## Two data sessions

### Session A: Browser (single-component, no banklån) — Oct 6
- Provenu: 2.500.000, Start: 06-10-2026, Horizon: 5yr, Tax: 33.6%
- No "Medtag ejendomsværdi og belåningsgrænser" → single realkredit component

| Alt | Label | Hovedstol | Gns.kurs | Udst.omk. | Kontant | Ydl. f.s. | Ydl. e.s. | ÅOP f.s. |
|-----|--------|-----------|----------|-----------|---------|-----------|-----------|----------|
| 1 | 30 år F3 januar | 2.546.000 | 100,00 | 45.267 | 2.500.733 | 13.031 | 10.053 | 4,48% |
| 2 | 30 år F5 januar | 2.547.000 | 100,00 | 46.818 | 2.500.182 | 13.324 | 10.200 | 4,67% |
| 3 | 30 år 4% obligation | 2.719.000 | 93,66 | 45.906 | 2.500.808 | 14.589 | 11.011 | 5,57% |

Horizon (0% shock):
| Alt | Rente | Afdrag | Ydelse | Restgæld | Gns.Kurs | Indfrielse | Periodeomk. |
|-----|-------|--------|--------|----------|----------|------------|-------------|
| 1 (F3) | 382.697 | 283.286 | 665.983 | 2.262.714 | 100,60 | 2.276.509 | 442.492 |
| 2 (F5) | 393.498 | 284.504 | 678.002 | 2.262.496 | 100,76 | 2.279.698 | 457.700 |
| 3 (4%) | 405.648 | 260.266 | 665.915 | 2.458.734 | 95,28 | 2.342.700 | 507.807 |

### Session B: Read-fetch (with banklån, two-component) — Oct 5
- Provenu: 2.500.000, Start: 05-10-2026, Horizon: 5yr, Tax: 33.6%
- Default with banklån (80% realkredit, 20% bank)

| Alt | Label | Hovedstol | Gns.kurs | Udst.omk. | Kontant | Ydl. f.s. | Ydl. e.s. | ÅOP f.s. |
|-----|--------|-----------|----------|-----------|---------|-----------|-----------|----------|
| 1 | F3+bank | 2.546.000 | 100,00 | 45.673 | 2.500.327 | 14.442 | 10.803 | 5,52% |
| 2 | F5+bank | 2.547.000 | 100,00 | 46.891 | 2.500.109 | 14.676 | 10.920 | 5,67% |
| 3 | 4%+bank | 2.682.000 | 94,95 | 46.176 | 2.500.479 | 15.666 | 11.556 | 6,34% |

## Exact loan parameters from detail pages (Session B, Oct 5)

### Alt 1: F3 januar + banklån
- **Realkredit**: type=F3, rate=3.23%, bidrag=0.95%, optagelseskurs=95.63
  - Hovedstol=2.000.000, obligationshovedstol=2.095.810
  - Kursværdi=2.000.000 (wait: 2.000.000 × 95.63/100 = 1.912.600 — but kursværdi says 2.000.000)
  - Udst.omk.=37.023 (ekspeditionsgebyr=5.000, kurtage=4.000, fast tinglysning=1.825, procentuel tinglysning=26.198)
  - Faktisk provenu (realkredit)=1.962.977
  - ÅOP før skat (realkredit)=4.49%, ÅOP før skat (total)=5.52%
- **Bank**: hovedstol=546.000, udst=8.650
  - Faktisk provenu (bank)=537.350
  - Bank rate ≈ 8.4% (derived from quarterly interest)

### Alt 2: F5 januar + banklån
- **Realkredit**: type=F5, rate=3.43%, bidrag=0.95%, optagelseskurs=91.43
  - Hovedstol=2.000.000, obligationshovedstol=2.192.306
  - Udst.omk.=38.229
  - ÅOP før skat (realkredit)=4.68%, ÅOP før skat (total)=5.67%

### Alt 3: 4% fast rente + banklån
- **Realkredit**: type=FIXED, rate=4.00%, bidrag=0.70%, optagelseskurs=93.76
  - Hovedstol=2.136.000, obligationshovedstol=2.136.000
  - Udst.omk.=37.526 (ekspeditionsgebyr=5.000, kurtage=4.001, fast tinglysning=1.825, procentuel tinglysning=26.700)
  - Faktisk provenu (realkredit)=1.963.129
  - ÅOP før skat (realkredit)=5.57%, ÅOP før skat (total)=6.34%

### Horizon (Session B, 0% shock):
| Alt | Rente | Afdrag | Ydelse | Restgæld | Gns.Kurs | Indfrielse | Periodeomk. |
|-----|-------|--------|--------|----------|----------|------------|-------------|
| 1 (F3+bank) | 416.855 | 235.834 | 652.689 | 2.310.166 | 100,52 | 2.322.131 | 474.492 |
| 2 (F5+bank) | 428.066 | 230.728 | 658.795 | 2.316.272 | 100,64 | 2.331.194 | 489.880 |
| 3 (4%+bank) | 468.628 | 229.630 | 698.259 | 2.452.370 | 96,33 | 2.362.430 | 560.210 |

### Horizon (Session B, -2% shock):
| Alt | Rente | Afdrag | Ydelse | Restgæld | Gns.Kurs | Indfrielse | Periodeomk. |
|-----|-------|--------|--------|----------|----------|------------|-------------|
| 1 (F3+bank) | 311.352 | 285.730 | 597.082 | 2.260.270 | 100,53 | 2.272.254 | 369.009 |
| 2 (F5+bank) | 374.329 | 251.663 | 625.992 | 2.295.337 | 100,67 | 2.310.617 | 436.501 |
| 3 (4%+bank) | 434.002 | 239.448 | 673.450 | 2.442.552 | 100,50 | 2.454.828 | 627.799 |

### Horizon (Session B, +2% shock):
| Alt | Rente | Afdrag | Ydelse | Restgæld | Gns.Kurs | Indfrielse | Periodeomk. |
|-----|-------|--------|--------|----------|----------|------------|-------------|
| 1 (F3+bank) | 524.946 | 195.830 | 720.776 | 2.350.170 | 100,50 | 2.361.963 | 582.411 |
| 2 (F5+bank) | 482.630 | 214.385 | 697.015 | 2.332.615 | 100,60 | 2.346.618 | 543.524 |
| 3 (4%+bank) | 503.753 | 222.266 | 726.018 | 2.459.734 | 86,08 | 2.117.419 | 342.958 |

## Key findings

### What matches exactly
1. **Hovedstol derivation** for F3 and F5: `_hovedstol_for_provenu(provenu, price, issue_pct)` produces exactly 2.000.000
2. **ÅOP for fixed-rate** (4% obligation): our effective annual rate = 5.57% matches reference 5.57%

### What doesn't match (known modeling differences)
1. **Payment frequency**: our engine uses monthly; reference uses quarterly (Danish realkredit pays quarterly)
2. **Flexlån ÅOP**: our engine amortizes the discount over 30 years; reference amortizes over the rate period (3yr for F3, 5yr for F5)
3. **Issue costs**: our model uses a single `issue_costs_pct`; reference has itemized costs (ekspeditionsgebyr, kurtage, tinglysningsafgift)
4. **Bond structure**: our engine uses simple annuity; reference uses Danish realkredit bond structure with multiple series
5. **Indfrielseskurs**: our engine uses a simple price model; reference uses Scanrate's OAS-based bond pricing model

### Effective rates derived from reference
- F3: eff_rate = 3.23% + 0.95% = 4.18%
- F5: eff_rate = 3.43% + 0.95% = 4.38%
- 4% fixed: eff_rate = 4.00% + 0.70% = 4.70%
- Bank (variabelt): rate ≈ 8.4% (derived from quarterly interest on bank hovedstol)
