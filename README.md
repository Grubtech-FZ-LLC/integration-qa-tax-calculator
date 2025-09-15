# Smart Cal

## 🎯 Purpose of the Project

Smart Cal is a comprehensive tax verification system designed specifically for restaurant and e-commerce platforms that need to validate tax calculations across complex discount scenarios. The project addresses the critical need for accurate tax computation in modern point-of-sale systems where multiple discount types can be applied simultaneously.

### Key Objectives:
- **Tax Accuracy Validation**: Ensure tax calculations are mathematically correct across all discount patterns
- **Pattern Recognition**: Automatically detect and handle 4 different discount combination scenarios
- **Data Integrity**: Verify that database tax amounts match calculated values for audit compliance
- **Error Detection**: Identify orders with missing or incorrect tax configurations
- **Regulatory Compliance**: Support tax-inclusive pricing models common in restaurant industries

### Business Value:
- Prevents revenue loss from incorrect tax calculations
- Ensures compliance with tax regulations
- Provides detailed audit trails for financial reporting
- Reduces manual tax verification workload
- Supports complex promotional discount strategies

## 🛠️ Project Setup Guidelines

### Prerequisites
- **Python**: Version 3.8 or higher
- **MongoDB**: Access to restaurant order database
- **Operating System**: Windows, macOS, or Linux
- **Memory**: Minimum 4GB RAM recommended
- **Storage**: 500MB free space for dependencies

### Environment Setup

#### 1. Clone Repository
```bash
git clone <repository-url>
cd smart_cal
```

#### 2. Create Virtual Environment
**Windows:**
```cmd
python -m venv .venv
.venv\Scripts\activate
```

**macOS/Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

#### 3. Install Dependencies
```bash
# Development installation (recommended)
pip install -e ".[dev]"

# Or production installation
pip install -r requirements.txt
```

#### 4. Environment Configuration
Create a `.env` file in the project root directory:

```env
# MongoDB Configuration
DB_CONNECTION_URL=mongodb://localhost:27017
DB_NAME=GRUBTECH_MASTER_DATA_STG_V2
COLLECTION_NAME=PARTNER_RESTAURANT_ORDER

# Application Settings
LOG_LEVEL=INFO
TAX_INCLUSIVE=true

# Optional: Security Settings
DB_USERNAME=your_username
DB_PASSWORD=your_password
```

#### 5. Verify Installation
```bash
# Test basic functionality
.venv\Scripts\python.exe -m smart_cal.cli --help

# Test database connection
.venv\Scripts\python.exe -m smart_cal.cli verify-order --order-id test
```

### Development Setup (Optional)

For contributors and developers:

```bash
# Install pre-commit hooks
pip install pre-commit
pre-commit install

# Run code quality checks
black src/ tests/
flake8 src/ tests/
pytest --cov=src/
```

## 🚀 Run Command in CLI

### Basic Order Verification Command

```bash
.venv\Scripts\python.exe -m smart_cal.cli verify-order --order-id {internal-orderId}
```

### Command Examples

#### Standard Order Verification
```bash
# Verify a specific order
.venv\Scripts\python.exe -m smart_cal.cli verify-order --order-id 1275743117322629120

# Verify with verbose output
.venv\Scripts\python.exe -m smart_cal.cli verify-order --order-id 1275743117322629120 --verbose

# Save output to file
.venv\Scripts\python.exe -m smart_cal.cli verify-order --order-id 1275743117322629120 > verification_report.txt
```

#### Environment Switching
```bash
# Verify order in staging environment (default)
.venv\Scripts\python.exe -m smart_cal.cli verify-order --order-id 1283965554531573760 --env staging

# Verify order in production environment  
.venv\Scripts\python.exe -m smart_cal.cli verify-order --order-id 1283969613210906624 --env production

# Using short aliases
.venv\Scripts\python.exe -m smart_cal.cli verify-order --order-id 1283965554531573760 --env stg
.venv\Scripts\python.exe -m smart_cal.cli verify-order --order-id 1283969613210906624 --env prod

# Available environments: staging, production, stg, prod
```

#### Pattern-Specific Testing
```bash
# Test Pattern 1: No discounts
.venv\Scripts\python.exe -m smart_cal.cli verify-order --order-id {pattern1-order-id}

# Test Pattern 2: Order-level discount only
.venv\Scripts\python.exe -m smart_cal.cli verify-order --order-id {pattern2-order-id}

# Test Pattern 3: Item-level discounts only
.venv\Scripts\python.exe -m smart_cal.cli verify-order --order-id {pattern3-order-id}

# Test Pattern 4: Combined discounts
.venv\Scripts\python.exe -m smart_cal.cli verify-order --order-id {pattern4-order-id}
```

#### Additional CLI Commands
```bash
# Show help and available commands
.venv\Scripts\python.exe -m smart_cal.cli --help

# Get help for specific command
.venv\Scripts\python.exe -m smart_cal.cli verify-order --help

# Calculate income tax (alternative feature)
.venv\Scripts\python.exe -m smart_cal.cli calculate-tax --income 50000 --year 2024
```

### Command Parameters

| Parameter | Required | Description | Example |
|-----------|----------|-------------|---------|
| `--order-id` | Yes | Internal order ID from MongoDB | `1275743117322629120` |
| `--verbose` | No | Enable detailed output | `--verbose` |
| `--log-file` | No | Save logs to specific file | `--log-file debug.log` |
| `--format` | No | Output format (text/json) | `--format json` |

### Expected Output Format

```
SMART CAL - TAX VERIFICATION ANALYSIS
=============================================
Order Reference: 1275743117322629120
Total Tax Amount (All Rates): $1.76909
Total Recomputed Tax:         $1.76909
Verification Status: PASSED - Tax calculations are accurate!

Discount Pattern: Pattern 3: Item-Level Discounts Only
Item-Level Discounts:   $0.50000
Order-Level Discount:   $0.00000

TAX BREAKDOWN BY RATE:
----------------------------------------
Tax Category: 68a697b6... (Rate: 10.0%)
   Expected (Database):    $1.76909
   Recomputed (Calculated): $1.76909
   Variance:               $0.00000 (Perfect Match!)

ITEMIZED TAX BREAKDOWN:
   -----------------------------------
   ITEM: Slow-Cooked Eggs Benedict (Quantity: 1)
      Unit Price:             $18.30000
      Item Total Price:       $17.95000
      Item Discount Applied:  $0.35000
      Taxable Amount:         $17.95000
      Expected Tax (DB):      $1.63182
      Recomputed Tax:         $1.63182
      Tax Variance:           $0.00000 (Perfect!)
```

### Error Handling

If the command encounters issues:

```bash
# Order not found
TAX ASSIGNMENT ERROR: Order ID not found in database.

# No tax assignments
TAX ASSIGNMENT ERROR: This menu doesn't have any taxes assigned.
Found 2 menu items and 4 modifiers, but all 'taxes' arrays are empty.

# Database connection issues
CONNECTION ERROR: Unable to connect to MongoDB. Check your .env configuration.
```

### Troubleshooting

#### Common Issues and Solutions

1. **Command not found**
   ```bash
   # Ensure virtual environment is activated
   .venv\Scripts\activate
   
   # Verify installation
   pip list | findstr smart-cal
   ```

2. **Database connection errors**
   ```bash
   # Check .env file exists and has correct values
   # Test MongoDB connection manually
   ```

3. **Permission errors**
   ```bash
   # Run as administrator on Windows
   # Check file permissions on macOS/Linux
   ```

## 📋 Supported Tax Calculation Patterns

### ✅ Pattern 1: No Discounts
- **Status**: IMPLEMENTED
- **Use Case**: Standard orders without any promotional discounts

### ✅ Pattern 2: Order-Level Discount Only  
- **Status**: IMPLEMENTED
- **Use Case**: Promotional codes applied to entire order

### ✅ Pattern 3: Item-Level Discounts Only
- **Status**: IMPLEMENTED  
- **Use Case**: Individual item promotions and BOGO offers

### ✅ Pattern 4: Combined Discounts
- **Status**: IMPLEMENTED
- **Use Case**: Complex promotions with both item and order-level discounts

## 🤝 Contributing

To contribute to Smart Cal:

1. Fork the repository
2. Create a feature branch
3. Follow the project setup guidelines above
4. Make your changes and add tests
5. Run quality checks before submitting
6. Create a pull request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

**Smart Cal** - Accurate tax verification for modern commerce platforms 🧮✨

