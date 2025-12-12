<div align="center">

# Integration QA Tax Calculator
Lightweight tax & discount pattern verification for restaurant / commerce orders.

</div>

## 1. Quick Start

### Prerequisites
- Python 3.8+ (tested up to 3.13)
- MongoDB access (read-only is enough)

### Setup
```bash
git clone https://github.com/Grubtech-FZ-LLC/integration-qa-tax-calculator.git
cd integration-qa-tax-calculator
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -e .
```

### .env (create in project root)
```env
DB_CONNECTION_URL=mongodb://localhost:27017
DB_NAME=GRUBTECH_MASTER_DATA_STG_V2
COLLECTION_NAME=PARTNER_RESTAURANT_ORDER
LOG_LEVEL=INFO
TAX_INCLUSIVE=true
```

---

## 2. Usage

### Windows (Batch File)
```cmd
verify-order.bat <order-id> [options]

# Examples:
.\verify-order.bat 1313394568122470400                    # staging (default)
.\verify-order.bat 1313394568122470400 -e prod            # production
.\verify-order.bat 1313394568122470400 -e prod -p         # with partner config
.\verify-order.bat 1313394568122470400 -t full -v         # full tax view + verbose
```

### Options
| Option | Description |
|--------|-------------|
| `-e`, `--env <stg\|prod>` | Environment (default: stg) |
| `-p`, `--show-partner-config` | Display partner configuration |
| `-t`, `--tax-view <level>` | Tax detail: `basic`, `full`, or `failures` |
| `--precision <2-8>` | Decimal precision (default: 5) |
| `-v`, `--verbose` | Enable verbose logging |

### Python (Direct)
```bash
python -m smart_cal.cli verify-order --order-id {id} --env stg
python -m smart_cal.cli verify-order --order-id {id} --env prod --show-partner-config
```

---

## 3. Discount & Tax Patterns

The calculator auto-detects one of four patterns based on discount presence. All formulas assume **tax-inclusive pricing**.

| Pattern | Item Discounts | Order Discount | Discounted Gross Formula |
|---------|----------------|----------------|---------------------------|
| 1 | No | No | `gross` |
| 2 | No | Yes | `gross - D_order × gross / Σgross` |
| 3 | Yes | No | `gross - itemDisc` |
| 4 | Yes | Yes | `(gross - itemDisc) - D_residual × postItem / ΣpostItem` |

**Tax calculation:** `exclusive = discountedGross / (1 + R)` → `tax = exclusive × rate`

---

## 4. Architecture

```
CLI (smart_cal.cli)
   → Verification Orchestrator
       → Repository (MongoDB)
       → Pattern Classifier
       → Tax Back-Out Engine
       → Validators
   → Renderer (CLI output)
```

---

## 5. Limitations
- Tax-inclusive pricing only
- Additive tax rates (no compounding)
- Discounts as currency amounts (not percentages)

---

License: MIT


