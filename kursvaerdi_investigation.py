"""Throwaway script: investigate kursværdi discrepancy (limitation 5).

Tests multiple hypotheses for why engine kursværdi ≠ PDF kursværdi
for Fast 5% obligationslån cases.
"""

from decimal import ROUND_CEILING, Decimal, getcontext

getcontext().prec = 50

HUNDRED = Decimal(100)
ZERO = Decimal(0)


def qceil(x):
    return x.to_integral_value(rounding=ROUND_CEILING)


def round_up_1000(x):
    return qceil(x / Decimal(1000)) * Decimal(1000)


# ── Fast 5% reference cases (from tests/test_engine.py REFERENCE_CASES) ──
cases = [
    {
        "id": "apr2023_fast5_20",
        "provenu": Decimal(2658348),
        "price": Decimal("99.52"),
        "issue_costs_nominal": Decimal(12166),
        "expected_hovedstol": Decimal(2686000),
    },
    {
        "id": "apr2023_fast5_21",
        "provenu": Decimal(2658891),
        "price": Decimal("98.52"),
        "issue_costs_nominal": Decimal(12167),
        "expected_hovedstol": Decimal(2714000),
    },
    {
        "id": "apr2023_fast5_22",
        "provenu": Decimal(2658891),
        "price": Decimal("98.52"),
        "issue_costs_nominal": Decimal(12167),
        "expected_hovedstol": Decimal(2714000),
    },
    {
        "id": "apr2023_fast5_23",
        "provenu": Decimal(2658891),
        "price": Decimal("98.52"),
        "issue_costs_nominal": Decimal(12167),
        "expected_hovedstol": Decimal(2714000),
    },
    {
        "id": "apr2023_fast5_25",
        "provenu": Decimal(2658530),
        "price": Decimal("98.83"),
        "issue_costs_nominal": Decimal(12166),
        "expected_hovedstol": Decimal(2705000),
    },
    {
        "id": "apr2023_fast5_30",
        "provenu": Decimal(2658530),
        "price": Decimal("98.83"),
        "issue_costs_nominal": Decimal(12166),
        "expected_hovedstol": Decimal(2705000),
    },
]

# ── Hypothesis: what is the "PDF kursværdi"? ──
# boligregner.dk help text says:
#   "Kursværdi: Lånets kursværdi beregnes som obligationshovedstolen ganget
#    med obligationskursen (tillagt eventuel prisskæring) og divideret med 100."
# = obligationshovedstol × (kurs + prisskæring) / 100
#
# The engine computes: kursværdi = hovedstol × price / 100
# where hovedstol = obligationshovedstol for FIXED loans.
#
# So the formula is the same UNLESS "prisskæring" (price haircut) is nonzero.
#
# Another possibility: the engine's hovedstol is rounded to 1000,
# but boligregner.dk computes kursværdi from the UNROUNDED hovedstol.
#
# Let's test all hypotheses.

print("=" * 90)
print("KURSVÆRDI INVESTIGATION — Fast 5% obligationslån")
print("=" * 90)

for c in cases:
    print(f"\n{'─' * 80}")
    print(f"Case: {c['id']}")
    print(
        f"  provenu={c['provenu']}, price={c['price']}, issue_costs_nominal={c['issue_costs_nominal']}"
    )
    print(f"  PDF expected hovedstol={c['expected_hovedstol']}")

    provenu = c["provenu"]
    price = c["price"]
    issue_nom = c["issue_costs_nominal"]

    # ── Engine hovedstol derivation (obligationslån, nominal mode) ──
    # raw = (provenu + nominal) / (price/100)
    # hovedstol = round_up_1000(raw)
    price_factor = price / HUNDRED
    raw_hoved = (provenu + issue_nom) / price_factor
    hovedstol = round_up_1000(raw_hoved)
    print(f"  Engine raw hovedstol (unrounded): {raw_hoved}")
    print(f"  Engine hovedstol (rounded to 1000): {hovedstol}")

    # ── Current engine kursværdi ──
    # kursvaerdi = hovedstol * price / 100  (line 645)
    engine_kursvaerdi = hovedstol * price / HUNDRED
    print(f"  Engine kursværdi (rounded hovedstol × price/100): {engine_kursvaerdi}")

    # ── Hypothesis 1: kursværdi from UNROUNDED hovedstol ──
    h1_kursvaerdi = raw_hoved * price / HUNDRED
    print(f"  H1: kursværdi from unrounded hovedstol: {h1_kursvaerdi}")
    print(f"      H1 = provenu + issue_costs_nominal = {provenu + issue_nom}")
    print(
        f"      (Because raw × price/100 = (provenu+nom)/(price/100) × price/100 = provenu+nom)"
    )
    h1_delta = engine_kursvaerdi - h1_kursvaerdi
    print(f"      Delta (engine - H1): {h1_delta}")

    # ── Hypothesis 2: prisskæring (price haircut/discount) ──
    # boligregner.dk: kursværdi = obligationshovedstol × (kurs + prisskæring) / 100
    # If prisskæring exists, it would make kursværdi different.
    # But we don't know prisskæring. Let's see if the delta implies one.
    # If PDF kursværdi = hovedstol × (price + prisskæring) / 100
    # and PDF kursværdi = provenu + issue_nom (i.e., the raw amount),
    # then prisskæring = (provenu + issue_nom) / hovedstol × 100 - price
    h2_implied_prisskaering = (provenu + issue_nom) / hovedstol * HUNDRED - price
    print(
        f"  H2: If PDF kursværdi = provenu + issue_nom, implied prisskæring: {h2_implied_prisskaering}"
    )

    # ── Hypothesis 3: kurtage deducted from kursværdi ──
    # kurtage is a broker fee. From reference data docs:
    #   udst.omk. = ekspeditionsgebyr + kurtage + fast tinglysning + procentuel tinglysning
    # The issue_costs_nominal in the test cases is the TOTAL udst.omk.
    # But kurtage may be deducted from the kursværdi SEPARATELY.
    # If kurtage ~4000 (from reference data: 4% fixed kurtage=4.001),
    # then kursværdi = hovedstol × price/100 - kurtage
    # But we don't know the kurtage split for these April 2023 cases.
    # Let's check: what kurtage would make engine_kursvaerdi match provenu + issue_nom?
    h3_required_kurtage = engine_kursvaerdi - (provenu + issue_nom)
    print(
        f"  H3: kurtage needed to make engine kursværdi = provenu + issue_nom: {h3_required_kurtage}"
    )

    # ── Hypothesis 4: kontant = kursværdi - udst.omk, and boligregner.dk
    #    reports kursværdi such that kontant = provenu exactly ──
    # If kontant = provenu, then kursværdi = provenu + udst.omk
    # = provenu + issue_costs_nominal
    h4_kursvaerdi = provenu + issue_nom
    h4_delta = engine_kursvaerdi - h4_kursvaerdi
    print(f"  H4: kursværdi = provenu + issue_nom (kontant=provenu): {h4_kursvaerdi}")
    print(f"      Delta (engine - H4): {h4_delta}")

    # ── Summary ──
    print(f"\n  SUMMARY for {c['id']}:")
    print(f"    Engine kursværdi:    {engine_kursvaerdi}")
    print(f"    H1 (unrounded):      {h1_kursvaerdi}  (delta: {h1_delta})")
    print(f"    H4 (=prov+issue_nom): {h4_kursvaerdi}  (delta: {h4_delta})")
    print(f"    Engine kontant:       {engine_kursvaerdi - issue_nom}")
    print(f"    Provenu:              {provenu}")
    print(f"    Kontant - Provenu:    {engine_kursvaerdi - issue_nom - provenu}")

print(f"\n{'=' * 90}")
print("ANALYSIS")
print("=" * 90)
print()
print("The engine computes:")
print("  1. raw_hovedstol = (provenu + issue_costs_nominal) / (price/100)")
print("  2. hovedstol = round_up_1000(raw_hovedstol)")
print("  3. kursværdi = hovedstol × price / 100")
print()
print("Because of the round_up_1000 in step 2, hovedstol >= raw_hovedstol.")
print("Therefore kursværdi = hovedstol × price/100 >= raw_hovedstol × price/100")
print("                   = (provenu + issue_nom) / (price/100) × price/100")
print("                   = provenu + issue_nom")
print()
print("So engine kursværdi >= provenu + issue_costs_nominal.")
print("The excess = (hovedstol - raw_hovedstol) × price / 100")
print("           = rounding_uplift × price / 100")
print()
print("boligregner.dk appears to compute kursværdi from the UNROUNDED hovedstol,")
print("which equals exactly provenu + issue_costs_nominal.")
print("The engine computes kursværdi from the ROUNDED hovedstol, which is larger.")
print()
print("This is the rounding-of-hovedstol hypothesis (H1).")
print()

# Verify: delta = (hovedstol - raw) × price / 100 for each case
print("Verification: delta = (rounded_hovedstol - raw_hovedstol) × price / 100")
for c in cases:
    provenu = c["provenu"]
    price = c["price"]
    issue_nom = c["issue_costs_nominal"]
    raw = (provenu + issue_nom) / (price / HUNDRED)
    hoved = round_up_1000(raw)
    engine_kv = hoved * price / HUNDRED
    delta = engine_kv - (provenu + issue_nom)
    rounding_uplift = hoved - raw
    computed_delta = rounding_uplift * price / HUNDRED
    print(
        f"  {c['id']}: rounding_uplift={rounding_uplift}, delta={delta}, "
        f"uplift×price/100={computed_delta}, match={abs(delta - computed_delta) < Decimal('0.01')}"
    )
