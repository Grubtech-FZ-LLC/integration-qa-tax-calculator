<div align="center">

# Smart Cal
Lightweight tax & discount pattern verification for restaurant / commerce orders.

</div>

## 1. Project Setup (Quick Start)

### Prerequisites
Python 3.8+ (tested up to 3.13)
MongoDB access (read-only is enough)
Windows / macOS / Linux

### Clone & Environment
```bash
git clone <repository-url>
cd smart_cal
python -m venv .venv
. .venv/Scripts/activate  # Windows CMD: .venv\Scripts\activate
```

### Install
Dev (recommended: editable + tooling):
```bash
pip install -e ".[dev]"
```
Or minimal runtime:
```bash
pip install -r requirements.txt
```

### .env (create in project root)
```env
DB_CONNECTION_URL=mongodb://localhost:27017
DB_NAME=GRUBTECH_MASTER_DATA_STG_V2
COLLECTION_NAME=PARTNER_RESTAURANT_ORDER
LOG_LEVEL=INFO
TAX_INCLUSIVE=true
```

### Usage Commands

Staging environment:
```bash
python -m smart_cal.cli verify-order --order-id {id} --env stg
```

Production environment:
```bash
python -m smart_cal.cli verify-order --order-id {id} --env prod
```

With partner configuration display:
```bash
python -m smart_cal.cli verify-order --order-id {id} --env prod --show-partner-config
```

Note: All calculations use 5-decimal precision. Default environment is staging if `--env` is omitted.

Output Style: The TAX VERIFICATION section now renders in a hierarchical tree view by default (no extra flags needed).

#### Optional Flags
| Flag | Description |
|------|-------------|
| `--show-partner-config` | Display partner configuration from PARTNER_APPLICATION collection. Queries based on order's partnerId, foodAggragetorId, restaurantId, and kitchenId to show matching brand and location configuration including menu settings. |
| `--tax-view {basic\|full\|failures}` | Tax detail level: `basic` (no aggregated table), `full` (include summary + reconciliation), `failures` (only rows with variances). Default: `basic` |
| `--precision {2-8}` | Decimal precision for tax calculations (default: 5). Controls decimal places in r_j/(1+R) formula and validation tolerances. |
| `--verbose` or `-v` | Enable verbose logging for debugging. |

---

## 2. Partner Configuration Display

The `--show-partner-config` flag enables display of partner application configuration alongside tax verification. This feature queries the `PARTNER_APPLICATION` collection using relational mapping from the order document.

### Relational Mapping
| Order Field | Maps To | Partner Collection Field |
|-------------|---------|--------------------------|
| partnerId | → | partnerId |
| foodAggragetorId | → | applicationId |
| restaurantId | → | brandId (in brandConfigurations) |
| kitchenId | → | locationId (in locationConfigurations) |

### Query Strategy
The feature uses MongoDB `$elemMatch` to perform nested queries:
1. Match `partnerId` and `applicationId` at document level
2. Match `brandId` within `brandConfigurations` array
3. Match `locationId` within `locationConfigurations` array

This ensures only the specific brand and location configuration matching the order is displayed (not all configurations in the document).

### Display Format
```
🏢 PARTNER CONFIGURATION
============================================================
   Status: FOUND ✅
   
      Partner ID:         62f1234567890abcdef123456
      Application ID:     5f9876543210fedcba987654
      Brand ID:           1234567890123456789
      
         ═══ Location Configuration ═══
         locationId:         9876543210987654321
         status:             active
         glovoLocationId:    12345
         
         menuConfiguration:
            menuId:  5f1234567890abcdef123456
            localeConfiguration:
               currencyCode:  AED
               timeZone:  Asia/Dubai
            bagFeeItemId:  5f9876543210fedcba987654
            deliveryFeeItemId:  5f1111222233334444555566
```

### Dynamic Field Discovery
The display is **aggregator-agnostic** and shows all fields dynamically:
- Only guaranteed common fields (`locationId`, `status`, `menuId`, `localeConfiguration`) have labels
- All other fields (e.g., `glovoLocationId`, `talabatLocationId`, etc.) are discovered and displayed automatically
- This ensures compatibility with any food aggregator without hardcoded field assumptions

### Use Cases
- Verify menu configuration matches expected settings
- Debug discrepancies in tax calculations related to menu setup
- Audit partner onboarding and configuration correctness
- Cross-reference order data with partner application settings

### Example
```bash
# Display partner config for production order
python -m smart_cal.cli verify-order --order-id 1303647969745534976 --env prod --show-partner-config
```

---

## 3. Discount & Tax Calculation Patterns

Smart Cal auto-detects one of four mutually exclusive patterns based on presence of item-level and order-level discounts. All formulas assume tax-inclusive pricing (common in food service). When taxes are inclusive, we back out tax from a discounted gross using the aggregated rate R = Σ r_j of all applicable tax rates to that line.

### Notation
| Symbol | Meaning |
|--------|---------|
| i | Item index |
| qty_i | Quantity of item i |
| unitPrice_i | Tax-inclusive unit price |
| gross_i = unitPrice_i * qty_i | Pre-discount line gross |
| itemDisc_i | Item-level discount applied directly to item i (0 if none) |
| D_order | Declared total order-level discount (from order header) |
| R_i = Σ r_{i,j} | Sum of tax rates (as decimal, e.g. 0.10) applied to item i |
| tolerance | Small numeric tolerance (≈1e-5) used in validation |

Tax back-out (inclusive): exclusive = discountedGross / (1 + R_i)
Per-rate tax: tax_{i,j} = exclusive * r_{i,j}
Net: net_i = discountedGross - Σ_j tax_{i,j}

### Pattern 1 – No Discounts
Condition: Σ itemDisc_i = 0 and D_order = 0

discountedGross_i = gross_i
exclusive_i = gross_i / (1 + R_i)
tax_{i,j} = exclusive_i * r_{i,j}
net_i = discountedGross_i - Σ_j tax_{i,j}

### Pattern 2 – Order-Level Discount Only
Condition: D_order > 0 and all itemDisc_i = 0

Allocate order discount proportionally over gross:
alloc_i = D_order * gross_i / Σ_k gross_k
discountedGross_i = gross_i - alloc_i
exclusive_i = discountedGross_i / (1 + R_i)
tax_{i,j} = exclusive_i * r_{i,j}
net_i = discountedGross_i - Σ_j tax_{i,j}

### Pattern 3 – Item-Level Discounts Only
Condition: Σ itemDisc_i > 0 and D_order = 0

postItem_i = gross_i - itemDisc_i
discountedGross_i = postItem_i
exclusive_i = discountedGross_i / (1 + R_i)
tax_{i,j} = exclusive_i * r_{i,j}
net_i = discountedGross_i - Σ_j tax_{i,j}

### Pattern 4 – Combined (Item + Order-Level)
Condition: Σ itemDisc_i > 0 and D_order > 0

Stage 1 (apply item discounts):
postItem_i = gross_i - itemDisc_i

Compute residual order discount actually remaining after item discounts:
Let D_residual = max( D_order - ( any previously embedded adjustments ), 0 )
(Implementation: we treat D_residual as the order-level portion not already attributed item-wise.)

Stage 2 (proportional allocation on post-item amounts):
residualAlloc_i = D_residual * postItem_i / Σ_k postItem_k
finalDiscountedGross_i = postItem_i - residualAlloc_i

Tax extraction:
exclusive_i = finalDiscountedGross_i / (1 + R_i)
tax_{i,j} = exclusive_i * r_{i,j}
net_i = finalDiscountedGross_i - Σ_j tax_{i,j}

Degeneration Rule: If D_residual ≤ tolerance the engine collapses Pattern 4 → Pattern 3 for validation (avoids false mismatches on negligible residuals).

### Pattern Summary Table
| Pattern | Item Discounts | Order Discount | Allocation Basis | Discounted Gross Formula |
|---------|----------------|----------------|------------------|---------------------------|
| 1 | No | No | – | gross_i |
| 2 | No | Yes | gross_i | gross_i - D_order * gross_i / Σ gross |
| 3 | Yes | No | – | gross_i - itemDisc_i |
| 4 | Yes | Yes | postItem_i | (gross_i - itemDisc_i) - D_residual * postItem_i / Σ postItem |

### Validation Notes
Rounding: Taxes are recomputed from exclusive amounts; minor net deltas tolerated (Patterns 2 & 4) because two-stage allocation can introduce fractional cent drift.
Strictness: Tax sums per rate must match within tolerance; discount distribution mismatches are flagged if proportional ratios deviate materially.

### CLI Example
```bash
python -m smart_cal.cli verify-order --order-id 1234567890123456789 --env stg
```

---

## 4. Architecture (At a Glance)

```
CLI (smart_cal.cli)
   → Verification Orchestrator (tax_calculation.verification.verify)
	   → Repository (tax_calculation.repository)   # Fetch order from Mongo
	   → Pattern Classifier & Allocators           # Decide pattern 1–4
	   → Tax Back-Out Engine                       # Inclusive → exclusive → per-rate tax
	   → Validators (menu/item consistency, charges, totals)
   → Renderer (CLI formatting & JSON output)
```

Key design choices:
- Pure functions where practical (easy to test)
- Proportional allocation for fairness & reversibility
- Tolerance-based comparison to avoid false negatives

## 5. Limitations & Assumptions
- Pricing is tax-inclusive (no tax-exclusive branch yet)
- Tax rates assumed additive (no compounding or cascading taxes)
- Discounts are currency-amount (not percentage) by the time they reach the engine
- No currency conversion / multi-currency normalization
- Repository expects a Mongo schema containing order-level taxes & line arrays
- Pattern 4 degeneration when residual discount is numerically insignificant (≤ tolerance)

## 6. Roadmap (Short List)
| Planned | Description |
|---------|-------------|
| Tax-exclusive mode | Support orders priced net of tax |
| Modifier-level audit | Reconcile nested modifiers / extras individually |
| Extended JSON schema | Rich structured output for automation consumers |
| Anomaly codes | Machine parsable reason tags (e.g. ALLOC_DRIFT, TAX_MISMATCH) |
| Configurable tolerance | CLI/env override instead of fixed constant |

## 7. Testing
Basic test invocation (once you add tests):
```bash
pytest -q
```

Suggested minimal tests to add:
| Test | Purpose |
|------|---------|
| test_pattern_detection.py | Verifies classification logic for synthetic orders |
| test_allocation_pattern2.py | Ensures proportional order-level discount distribution |
| test_pattern4_two_stage.py | Validates residual allocation and degeneration |
| test_tax_rounding.py | Confirms back-out vs stored tax within tolerance |

Fixture idea: store 1 synthetic JSON per pattern under `tests/fixtures/`.

## 8. Extending the Engine
| Task | Where |
|------|-------|
| Add new pattern detection | Extend logic inside `verification.py` (classifier section) |
| Alternate tax strategy | Inject a new back-out function & swap in orchestration layer |
| Custom output formats | Add renderer functions in `cli.py` for different output formats |
| Additional validations | Append new validator & include its result in summary object |
| Environment source | Enhance `repository.py` to read more env key variants |

Guiding principle: keep calculation pure; isolate I/O (DB, CLI) at the boundaries.


License: MIT (see LICENSE).

> This README intentionally focuses ONLY on setup + core discount/tax logic per request. For extended consistency checks, CLI formatting examples, and future roadmap, refer to Git history of earlier README versions.


