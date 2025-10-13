"""
Command-line interface for Smart Cal.
"""

import argparse
import os
import sys
from typing import Optional
from dotenv import load_dotenv

from . import __version__
from .utils.logging import setup_logging
from .tax_calculation.verification import TaxVerificationService


def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        description="Smart Cal - MongoDB Order Tax Verification Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  smart-cal --version
  smart-cal --help
  smart-cal verify-order --order-id 1283987880027074560 --env production
  smart-cal verify-order --order-id 1283965554531573760 --env staging
        """,
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version=f"Smart Cal {__version__}",
    )
    
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    
    parser.add_argument(
        "--log-file",
        help="Log file path",
    )
    
    # Create subparsers for different commands
    subparsers = parser.add_subparsers(
        dest="command",
        help="Available commands",
    )
    
    # Order tax verification command
    verify_parser = subparsers.add_parser(
        "verify-order",
        help="Verify tax calculations for a MongoDB order",
    )
    verify_parser.add_argument(
        "--order-id",
        type=str,
        required=True,
        help="Internal ID of the order to verify",
    )
    verify_parser.add_argument(
        "--env",
        type=str,
        choices=["staging", "production", "stg", "prod"],
        default="staging",
        help="Database environment (default: staging)",
    )
    
    verify_parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    verify_parser.add_argument(
        "--tax-view",
        choices=["basic", "full", "failures"],
        default="basic",
        help="Tax view detail level: basic (no aggregated table), full (include summary + reconciliation), failures (only rows with variances)."
    )
    
    verify_parser.add_argument(
        "--precision",
        type=int,
        default=5,
        choices=[2, 3, 4, 5, 6, 7, 8],
        help="Decimal precision for tax calculations (default: 5). Controls the number of decimal places used in r_j/(1+R) formula and validation tolerances."
    )

    
    return parser


def verify_order_tax(order_id: str, environment: str = "staging", tax_view: str = "basic", precision: int = 5) -> None:
    """
    Verify tax calculations for a MongoDB order with enhanced precision support.
    
    Args:
        order_id: Internal ID of the order to verify
        environment: Database environment ("staging", "production", "stg", "prod")
        tax_view: Tax detail level ("basic", "full", "failures")
        precision: Decimal precision for tax calculations (2-8, default: 5)
    """
    logger = setup_logging()
    logger.info(f"Verifying tax for order: {order_id} in {environment} environment")
    
    # Load environment variables from .env file
    load_dotenv('.env')
    
    # Map environment aliases
    env_map = {
        "staging": "stg",
        "stg": "stg", 
        "production": "prod",
        "prod": "prod"
    }
    env_key = env_map.get(environment.lower(), "stg")
    
    # Database configuration mapping
    db_configs = {
        "stg": {
            "db_name_key": "DB_NAME_STG",
            "connection_url": "DB_CONNECTION_URL_STG"  # Environment-specific URL
        },
        "prod": {
            "db_name_key": "DB_NAME_PROD", 
            "connection_url": "DB_CONNECTION_URL_PROD"  # Environment-specific URL
        }
    }
    
    config = db_configs[env_key]
    # Get environment-specific database name, fallback to default
    db_name = os.getenv(config["db_name_key"]) or os.getenv('DB_NAME')
    
    # Defer printing header until we have aggregator. Collect lines then render boxed.
    header_lines = []
    header_lines.append(("Environment", environment.upper()))
    header_lines.append(("Database", db_name))
    
    try:
        # Pass environment-specific connection URL with precision parameter
        verification_service = TaxVerificationService(
            db_name=db_name,
            connection_url_env_key=config["connection_url"],
            precision=precision
        )
        result = verification_service.verify_order_by_id(order_id)
    except ValueError as e:
        # Handle tax assignment errors gracefully
        error_msg = str(e)
        if "TAX ASSIGNMENT ERROR" in error_msg:
            print(f"\n{error_msg}")
            print(f"\nRECOMMENDATION:")
            print(f"- Check menu configuration to ensure tax categories are assigned")
            print(f"- Verify that tax rules are properly set up for menu items")
            print(f"- Contact system administrator if tax setup is needed")
            return
        else:
            # Re-raise other ValueError types
            raise
    except Exception as e:
        logger.error(f"Error: {e}")
        return
    
    # Top summary intentionally suppressed per latest requirement
    summary = result.get('summary', {})
    if 'foodAggragetorId' in summary:
        header_lines.append(("foodAggragetorId", summary['foodAggragetorId']))
    header_lines.append(("Order ID", order_id))
    
    # Add pattern information to header
    if 'pattern_info' in summary:
        pattern_info = summary['pattern_info']
        pattern_text = pattern_info.get('pattern', 'Unknown')
        header_lines.append(("Pattern", pattern_text))

    # Build boxed header
    label_width = max(len(lbl) for lbl, _ in header_lines)
    # Inner content width: label + 2 spaces around ':' + space + value
    inner_width = max(len(f" {lbl.ljust(label_width)} : {val}") for lbl, val in header_lines)
    top = "┌" + "─" * (inner_width) + "┐"
    bottom = "└" + "─" * (inner_width) + "┘"
    print(top)
    for lbl, val in header_lines:
        line = f" {lbl.ljust(label_width)} : {val}"
        padding = inner_width - len(line)
        print(f"│{line + ' '*padding}│")
    print(bottom)
    
    # Show discount amounts and warnings (if any) outside the box
    if 'pattern_info' in summary:
        pattern_info = summary['pattern_info']
            
        # Show discount amounts
        discount_details = []
        if pattern_info.get('item_discounts', 0) > 0:
            discount_details.append(f"Item-Level Discounts:   ${pattern_info['item_discounts']:.5f}")
        if pattern_info.get('order_discount', 0) > 0:
            discount_details.append(f"Order-Level Discount:   ${pattern_info['order_discount']:.5f}")
        if pattern_info.get('remaining_order_discount', 0) > 0:
            discount_details.append(f"Remaining Order Disc:   ${pattern_info['remaining_order_discount']:.5f}")
        
        if discount_details:
            print("\nDISCOUNT BREAKDOWN:")
            print("=" * 60)
            for detail in discount_details:
                print(detail)
            
        # Display discount validation warnings if present
        if 'discount_valid' in pattern_info and not pattern_info['discount_valid']:
            print(f"\n\033[1m\033[33m⚠️  DISCOUNT VALIDATION WARNING ⚠️\033[0m")
            if pattern_info.get('discount_warning'):
                print(f"\033[33m{pattern_info['discount_warning']}\033[0m")
            print(f"\033[33mRecommendation: Check the original order payload and discount application logic.\033[0m")
    
    # Defer building full TAX VERIFICATION block until we also know charges
    taxes = result.get('taxes', [])
    enhanced_block_lines = []  # will be populated after charges_validation extraction
    total_expected = total_recomputed = total_variance = 0.0
    overall_status = "N/A"

    
    # New: menuDetails price calculations validation section
    menu_validation = summary.get('menu_calculations_validation')
    if menu_validation:
        print("\nMENU DETAILS VALIDATION:")
        print("=" * 60)
        
        total_items = menu_validation.get('total_items', 0)
        is_valid = menu_validation.get('is_valid', False)
        calculation_errors = menu_validation.get('calculation_errors', [])
        validation_details = menu_validation.get('validation_details', [])
        status_colored = "\033[1;32mPASSED\033[0m" if is_valid else "\033[1;31mFAILED\033[0m"
        print(f"   Overall Status: {status_colored}")
        print(f"   Items Validated: {total_items}")
        
        if calculation_errors:
            print(f"   Calculation Errors Found: {len(calculation_errors)}")
        
        # Show detailed calculation validation for each item
        if validation_details:
            # Helper to print a table of calculations
            def _print_calc_table(prefix: str, calculations: dict):
                # Determine dynamic width based on longest field name (min baseline 22)
                longest_field = max([len(n) for n in calculations.keys()] + [5]) if calculations else 5
                field_col_width = max(22, longest_field + 2)  # padding for readability
                header = f"{'Field':<{field_col_width}}{'Actual':>14}{'Expected':>14}{'Delta':>16}"
                sep = '-' * len(header)
                print(f"{prefix}{header}")
                print(f"{prefix}{sep}")
                for fname, data in calculations.items():
                    actual = data.get('actual', 0)
                    expected = data.get('expected', 0)
                    delta = data.get('delta', 0)
                    is_calc_valid = data.get('is_valid', False)
                    formula = data.get('formula', '')
                    icon = '✅' if is_calc_valid else '❌'
                    # icon + space consumes 2 chars; adjust field width accordingly
                    # Delta precision reduced from 8 to 5 per latest requirement
                    print(f"{prefix}{icon} {fname:<{field_col_width-2}}{actual:>14.5f}{expected:>14.5f}{delta:>16.5f}")
                    if not is_calc_valid and formula:
                        # Indent formula under the row (align under first data column)
                        print(f"{prefix}{'':2}Formula: {formula}")

            for item_validation in validation_details:
                item_name = item_validation.get('item_name', 'Unknown Item')
                # Prefer Mongo _id over item_id; handle dict ObjectId {'$oid': ...}
                raw_item_id = item_validation.get('_id') or item_validation.get('item_id')
                if isinstance(raw_item_id, dict) and '$oid' in raw_item_id:
                    raw_item_id = raw_item_id['$oid']
                item_id = raw_item_id or 'Unknown ID'
                qty = item_validation.get('qty', 1)
                tax_rate = item_validation.get('tax_rate', 0)

                print(f"\n   📦 Item: {item_name} (ID: {item_id})")
                print(f"      Qty: {qty}, Tax Rate: {tax_rate:.2f}%")
                _print_calc_table("      ", item_validation.get('calculations', {}))

                # Modifiers section
                modifiers = item_validation.get('modifiers', [])
                for modifier_validation in modifiers:
                    mod_name = modifier_validation.get('item_name', 'Unknown Modifier')
                    raw_mod_id = modifier_validation.get('_id') or modifier_validation.get('item_id')
                    if isinstance(raw_mod_id, dict) and '$oid' in raw_mod_id:
                        raw_mod_id = raw_mod_id['$oid']
                    mod_id = raw_mod_id or 'Unknown ID'
                    mod_qty = modifier_validation.get('qty', 1)
                    mod_tax_rate = modifier_validation.get('tax_rate', 0)
                    print(f"\n      🔧 Modifier: {mod_name} (ID: {mod_id})")
                    print(f"         Qty: {mod_qty}, Tax Rate: {mod_tax_rate:.2f}%")
                    _print_calc_table("         ", modifier_validation.get('calculations', {}))
        
        if not is_valid:
            validation_note = menu_validation.get('validation_note', 
                'Some price calculations in menuDetails are mathematically inconsistent!')
            discount_context = menu_validation.get('discount_context', '')
            
            print(f"\n   ⚠️  {validation_note}")
            if discount_context:
                print(f"   🔍 Context: {discount_context}")
            print(f"   💡 This may indicate data integrity issues or expected discount processing behavior.")
    
    # Optional: menuDetails vs itemDetails consistency section
    consistency = summary.get('menu_item_consistency')
    # ANSI color helper for PASSED/FAILED
    def _status_label(ok: bool) -> str:
        return "\033[1;32mPASSED\033[0m" if ok else "\033[1;31mFAILED\033[0m"
    if consistency:
        print("\nMENU / ITEM DETAILS CONSISTENCY:")
        print("=" * 60)
        if not consistency.get('available'):
            print(f"   ItemDetails not present ({consistency.get('reason','')}). Skipping comparison.")
        else:
            status = _status_label(consistency.get('is_consistent'))
            print(f"   Overall Status: {status}")
            print(f"   Items Compared: {consistency.get('total_compared',0)}")
            unmatched_menu = consistency.get('unmatched_in_menu',0)
            unmatched_item = consistency.get('unmatched_in_item',0)
            if unmatched_menu or unmatched_item:
                print(f"   Unmatched - menuDetails: {unmatched_menu}, itemDetails: {unmatched_item}")
            
            # Show detailed field-by-field comparison for all items
            items_detail = consistency.get('items_detail', [])
            if items_detail:
                # Organize items and modifiers: build parent -> modifiers mapping
                parent_items = []
                modifiers_by_parent = {}
                for item_detail in items_detail:
                    if item_detail.get('is_modifier'):
                        parent_key = item_detail.get('parent_key')
                        modifiers_by_parent.setdefault(parent_key, []).append(item_detail)
                    else:
                        parent_items.append(item_detail)

                # Common formatting parameters
                tolerance_val = consistency.get('tolerance', 1e-5)
                field_col_width = 26  # longest field names like taxExclusiveDiscountAmount
                header = f"{'':2}{'Field':<{field_col_width}}{'Menu':>12}{'Item':>12}{'Delta':>12}"
                sep_line = "".ljust(len(header), '-')

                def _print_rows(prefix_spaces: str, fields_dict: dict):
                    for fname, data in fields_dict.items():
                        menu_val = data.get('menu_value', 0)
                        item_val = data.get('item_value', 0)
                        delta = data.get('delta', 0)
                        is_match = abs(delta) <= tolerance_val
                        icon = "✅" if is_match else "❌"
                        if fname == 'qty':
                            menu_fmt = f"{int(menu_val)}"
                            item_fmt = f"{int(item_val)}"
                            delta_fmt = f"{int(delta)}"
                        else:
                            menu_fmt = f"{menu_val:.5f}"
                            item_fmt = f"{item_val:.5f}"
                            delta_fmt = f"{delta:.5f}"
                        print(f"{prefix_spaces}{icon} {fname:<{field_col_width}}{menu_fmt:>12}{item_fmt:>12}{delta_fmt:>12}")

                for item_detail in parent_items:
                    item_key = item_detail.get('key', 'Unknown')
                    item_name = item_detail.get('name', 'Unknown Item')
                    # Unified item header icon
                    print(f"\n   📦 Item: {item_name} (ID: {item_key})")
                    print(f"      {header}")
                    print(f"      {sep_line}")
                    _print_rows("      ", item_detail.get('fields', {}))

                    # Render modifiers for this item
                    modifiers = modifiers_by_parent.get(item_key, [])
                    for idx, mod_detail in enumerate(modifiers):
                        mod_key = mod_detail.get('key', 'Unknown')
                        mod_name = mod_detail.get('name', 'Unknown Modifier')
                        print(f"\n      🔧 Modifier: {mod_name} (ID: {mod_key})")
                        if mod_detail.get('note'):
                            print(f"         Note: {mod_detail.get('note')}")
                        # Header for modifiers (repeat for clarity if multiple)
                        print(f"         {header}")
                        print(f"         {sep_line}")
                        _print_rows("         ", mod_detail.get('fields', {}))
            else:
                diffs = consistency.get('differences', [])
                if diffs:
                    print(f"   Differences (showing up to 5):")
                    for d in diffs[:5]:
                        locator = d.get('key', d.get('index','?'))
                        field = d.get('field')
                        delta = d.get('delta')
                        menu_val = d.get('menu_value')
                        item_val = d.get('item_value')
                        print(f"      [{locator}] {field}: menu={menu_val} item={item_val} delta={delta}")
                    if len(diffs) > 5:
                        print(f"      ... {len(diffs)-5} more differences not shown")
                else:
                    print("   ✅ All fields match perfectly across all items!")

    # Charges validation section (data capture only for integration into TAX VERIFICATION block)
    charges_validation = summary.get('charges_validation')
    charge_entries = []
    charge_errors = []
    included_count = None
    charges_status_str = None
    if charges_validation:
        charge_entries = charges_validation.get('charge_entries', [])
        included_count = charges_validation.get('included_charge_count')
        charges_status_str = _status_label(charges_validation.get('is_valid'))
        charge_errors = charges_validation.get('errors', [])

    # Build the unified TAX VERIFICATION block now (taxes + charges + tax summary)
    if taxes:
        def _fmt_money(val: float) -> str:
            return f"${val:.{precision}f}"
        def _status_icon(delta: float) -> str:
            return "✅" if abs(delta) < 1e-5 else ("⚠️" if abs(delta) < 0.01 else "❌")
        total_expected = sum(t.get('expected_total', 0.0) for t in taxes)
        total_recomputed = sum(t.get('recomputed_total', 0.0) for t in taxes)
        total_variance = total_expected - total_recomputed
        overall_status = "✅ PASSED" if abs(total_variance) < 1e-4 else "⚠️ VARIANCES DETECTED"
        simple_status = "PASSED" if "PASSED" in overall_status else "VARIANCES DETECTED"

        print("\n🔍 TAX VERIFICATION")
        print("=" * 60)
        # Colorize overall status (green for PASSED, yellow for variances) for consistency with other sections
        if simple_status == "PASSED":
            simple_status_colored = "\033[1;32mPASSED\033[0m"
        else:
            # Use yellow for variance state to differentiate from hard failure (red)
            simple_status_colored = f"\033[1;33m{simple_status}\033[0m"
        print(f"   Overall Status: {simple_status_colored}")
        print("")

        # Build tax info from orderTaxes section first
        # Summary information
        order_taxes_section = result.get('orderTaxes', [])  # This should have the orderTaxes array
        # Handle both ObjectId format and string format for _id
        order_tax_lookup = {}
        for ot in order_taxes_section:
            tax_id = ot.get('_id')
            if isinstance(tax_id, dict) and '$oid' in tax_id:
                tax_id = tax_id['$oid']
            elif tax_id:
                tax_id = str(tax_id)
            if tax_id:
                order_tax_lookup[tax_id] = ot
                # Also create lookup for the short form (last 8 chars) since charges might reference that
                short_id = tax_id[-8:] if len(tax_id) > 8 else tax_id
                order_tax_lookup[short_id] = ot
        
        # Build mapping of tax_id -> list of (charge, tax_fragment) for charges referencing that tax
        charges_by_tax = {}
        for ch in charge_entries:
            for tx in ch.get('taxes', []) or []:
                tid = tx.get('taxId')
                if tid:
                    charges_by_tax.setdefault(tid, []).append((ch, tx))
                    # Also map by full tax ID if we can find it in orderTaxes
                    if len(tid) == 8:  # Short ID format
                        for full_id in order_tax_lookup.keys():
                            if full_id.endswith(tid) and len(full_id) > 8:
                                charges_by_tax.setdefault(full_id, []).append((ch, tx))
                                break
        
        # Create a comprehensive list of all tax IDs (from menu + charges)
        all_tax_ids = set()
        menu_tax_ids = {t.get('tax_id') for t in taxes}
        charge_tax_ids = set(charges_by_tax.keys())
        all_tax_ids = menu_tax_ids.union(charge_tax_ids)
        
        # Also try tax reconciliation as backup
        tax_recon = result.get('summary', {}).get('tax_reconciliation', [])
        recon_lookup = {tr.get('tax_id'): tr for tr in tax_recon}
        
        # Enhance existing menu taxes with proper names from orderTaxes
        enhanced_taxes = []
        for tax in taxes:
            tax_id = tax.get('tax_id')
            order_tax_info = order_tax_lookup.get(tax_id, {})
            enhanced_tax = dict(tax)
            enhanced_tax['tax_name'] = order_tax_info.get('name', 'Tax')
            enhanced_taxes.append(enhanced_tax)
        
        # Add charge-only taxes
        for charge_tax_id in charge_tax_ids:
            if charge_tax_id not in menu_tax_ids:
                # Get tax info from orderTaxes or reconciliation - try multiple approaches
                order_tax_info = order_tax_lookup.get(charge_tax_id, {})
                
                # Try to find matching tax info from reconciliation using various ID patterns
                recon_info = recon_lookup.get(charge_tax_id, {})
                if not recon_info:
                    # Try looking for full tax ID that ends with the charge tax ID
                    for recon_id, recon_data in recon_lookup.items():
                        if recon_id.endswith(charge_tax_id) and len(recon_id) > len(charge_tax_id):
                            recon_info = recon_data
                            break
                
                if not order_tax_info and len(charge_tax_id) == 8:
                    # If short ID didn't work, try to find full ID that ends with this
                    for full_id, tax_info in order_tax_lookup.items():
                        if full_id.endswith(charge_tax_id) and len(full_id) > 8:
                            order_tax_info = tax_info
                            break
                
                # Try to get tax name from reconciliation data if available
                # Common tax names based on ID patterns - this is a fallback approach
                tax_name = order_tax_info.get('name') or recon_info.get('name')
                if not tax_name:
                    # Fallback based on known patterns - charges are often VAT
                    if recon_info.get('charges_tax', 0) > 0 and recon_info.get('menu_tax', 0) == 0:
                        tax_name = 'VAT'  # Charges-only tax is typically VAT
                    else:
                        tax_name = 'Tax'  # Default for menu taxes
                tax_rate = order_tax_info.get('rate', recon_info.get('rate', 0.0))
                charges_tax = recon_info.get('charges_tax', 0.0)
                
                # Find the full tax ID for display - use reconciliation key if it's longer
                display_tax_id = charge_tax_id
                for recon_id in recon_lookup.keys():
                    if recon_id.endswith(charge_tax_id) and len(recon_id) > len(display_tax_id):
                        display_tax_id = recon_id
                        break
                
                synthetic_tax = {
                    'tax_id': display_tax_id,
                    'tax_name': tax_name,
                    'tax_rate': tax_rate,
                    'expected_total': charges_tax,
                    'recomputed_total': charges_tax,
                    'details': {'items': [], 'modifiers': []}
                }
                enhanced_taxes.append(synthetic_tax)
        
        extended_taxes = enhanced_taxes
        
        # --- Rendering strategies ---

        def _render_tree():
            print("TAX VERIFICATION TREE")
            print("-" * 60)
            for idx, tax_info in enumerate(extended_taxes, start=1):
                tax_id = tax_info.get('tax_id', 'Unknown')
                tax_rate = tax_info.get('tax_rate', 0.0)
                expected_total = float(tax_info.get('expected_total', 0.0))
                recomputed_total = float(tax_info.get('recomputed_total', 0.0))
                variance = expected_total - recomputed_total
                status_icon = '✅' if abs(variance) < 1e-5 else ('⚠️' if abs(variance) < 0.01 else '❌')
                tax_name = tax_info.get('tax_name', 'Tax')
                charges_only = False
                details = tax_info.get('details', {})
                items = details.get('items') or []
                if not items:
                    # If no items but has expected/recomputed (and appears in charges_by_tax) treat as charges-only
                    if charges_by_tax.get(tax_id):
                        charges_only = True
                header_extra = " (Charges-only)" if charges_only and items == [] else ""
                # Header: use consistent labelled segments for readability
                # Example: └─ VAT (14.00%) | ID: 5f...4088 | Total Tax: $7.12281 | ✅ (Charges-only)
                print(
                    f"└─ {tax_name} ({tax_rate:.2f}%) | ID: {tax_id} | Total Tax: {_fmt_money(recomputed_total)} | {status_icon}{header_extra}"
                )

                child_sections = []
                if items:
                    child_sections.append('items')
                related_charges = charges_by_tax.get(tax_id, [])
                if related_charges:
                    child_sections.append('charges')
                child_count = len(child_sections) + 1  # variance line

                # Items block (including modifiers under their parent items)
                if items:
                    # Get modifiers as well
                    modifiers = details.get('modifiers') or []
                    
                    # Organize modifiers by parent item
                    modifiers_by_parent = {}
                    for mod in modifiers:
                        parent_item_id = mod.get('parent_item_id')
                        if parent_item_id:
                            modifiers_by_parent.setdefault(str(parent_item_id), []).append(mod)
                    
                    # Compute dynamic widths (include items + modifiers names)
                    all_entries = items + modifiers
                    name_w = min(max(len(entry.get('name', 'Unknown')) for entry in all_entries), 40)
                    taxable_vals = [float(entry.get('taxable_amount', 0.0)) for entry in all_entries] or [0.0]
                    tax_vals = [float(entry.get('expected', 0.0)) for entry in all_entries] or [0.0]
                    taxable_w = max(len(f"{v:.2f}") for v in taxable_vals)
                    tax_w = max(len(f"{v:.5f}") for v in tax_vals)

                    # We reserve 2 chars for status/icon plus a space => 3, so cell = icon + space + name
                    # For modifiers we prepend "└─ " (3 chars) + icon + space + name
                    # Unify by defining a fixed cell width that fits the longest pattern
                    item_cell_width = name_w + 3  # 3 accounts for status prefix or tree prefix equivalence

                    print(f"   ├─ Items ({len(items)})" + (f" + Modifiers ({len(modifiers)})" if modifiers else ""))
                    # Header (blank space where icon/tree would be) so columns line up
                    print(
                        f"   │  {'Item':<{item_cell_width}}  Qty  Taxable{'':{max(0, taxable_w-7)}}  Tax"
                    )
                    print(
                        f"   │  {'-'*item_cell_width}  ---  {'-'*taxable_w}  {'-'*tax_w}"
                    )

                    for item in items:
                        item_name_full = item.get('name', 'Unknown Item')
                        # Truncate name if needed
                        item_name_trunc = item_name_full[:name_w] if len(item_name_full) > name_w else item_name_full
                        qty = item.get('qty', 1)
                        expected = float(item.get('expected', 0.0))
                        recomputed = float(item.get('recomputed', 0.0))
                        diff = expected - recomputed
                        taxable = float(item.get('taxable_amount', 0.0))
                        icon = _status_icon(diff)
                        cell_content = f"{icon} {item_name_trunc}"  # icon + space + name
                        cell = cell_content.ljust(item_cell_width)
                        print(
                            f"   │  {cell}  {qty:>3}  {taxable:>{taxable_w}.2f}  {expected:>{tax_w}.5f}"
                        )

                        # Modifiers under this item
                        internal_id = item.get('internal_id')
                        item_modifiers = modifiers_by_parent.get(str(internal_id), []) if internal_id else []
                        for modifier in item_modifiers:
                            mod_name_full = modifier.get('name', 'Unknown Modifier')
                            # Available space for modifier name after tree + icon + space
                            # Pattern: "└─ {icon} {name}" -> prefix_len = len("└─ ") + len(icon) + 1
                            # Use correct field names from verification module
                            mod_expected = float(modifier.get('expected_tax', modifier.get('expected', 0.0)))
                            mod_recomputed = float(modifier.get('recomputed_tax_final', modifier.get('recomputed', mod_expected)))
                            mod_icon = _status_icon(mod_expected - mod_recomputed)
                            prefix = f"└─ {mod_icon} "
                            avail_len = item_cell_width - len(prefix)
                            if avail_len < 0:
                                avail_len = 0
                            mod_name_trunc = mod_name_full[:avail_len] if len(mod_name_full) > avail_len else mod_name_full
                            mod_cell = f"{prefix}{mod_name_trunc}".ljust(item_cell_width)
                            mod_qty = modifier.get('qty', 1)
                            mod_taxable = float(modifier.get('taxable_amount', 0.0))
                            print(
                                f"   │  {mod_cell}  {mod_qty:>3}  {mod_taxable:>{taxable_w}.2f}  {mod_expected:>{tax_w}.5f}"
                            )
                # Charges block
                if related_charges:
                    print(f"   ├─ Charges ({len(related_charges)})")
                    # Pre-calculate width for aligned columns (charges are usually few, so single pass is fine)
                    # Collect numeric strings to determine dynamic widths (fallback to defaults)
                    base_strs = []
                    net_strs = []
                    tax_strs = []
                    for (ch_tmp, _tx) in related_charges:
                        base_strs.append(f"{ch_tmp.get('amount', 0.0):.2f}")
                        net_strs.append(f"{ch_tmp.get('taxExclusiveAmount', 0.0):.2f}")
                        tax_strs.append(f"{ch_tmp.get('tax', 0.0):.5f}")
                    base_w = max([len(s) for s in base_strs] + [6])  # at least width 6
                    net_w = max([len(s) for s in net_strs] + [6])
                    tax_w = max([len(s) for s in tax_strs] + [8])
                    type_w = 20
                    for c_idx, (ch, tx_frag) in enumerate(related_charges):
                        ch_type = str(ch.get('type'))[:type_w]
                        base_amount = ch.get('amount', 0.0)
                        tax_excl = ch.get('taxExclusiveAmount', 0.0)
                        full_tax = ch.get('tax', 0.0)
                        match_icon = '✅' if ch.get('recomputed_tax_match') else ('❌' if ch.get('recomputed_tax') is not None else '-')
                        print(
                            f"   │  {match_icon} {ch_type:<{type_w}} "
                            f"Base:{base_amount:>{base_w}.2f}  "
                            f"Net:{tax_excl:>{net_w}.2f}  "
                            f"Tax:{full_tax:>{tax_w}.5f}"
                        )
                # Variance line
                variance_label = f"Δ {variance:+.5f}"
                variance_status = "OK" if abs(variance) < 1e-5 else ("Within tolerance" if abs(variance) < 1e-4 else "Out of tolerance")
                print(f"   └─ Variance: {variance_label} ({variance_status})")
                print("")

        # Tree view is now the default (and only) style per latest requirement
        _render_tree()



        # Display any charge errors at the end of tax verification
        if charge_errors:
            print("\n   ⚠️ CHARGE ISSUES:")
            for e in charge_errors:
                print(f"      - {e.get('issue')}: {e.get('message')}")

        # Summary of tax verification (order-level aggregation)
        print("\n📋 ORDER TAXES VALIDATION:")
        
        # Calculate total tax amount for each tax ID and compare with orderTaxes array
        calculated_taxes_by_id = {}
        order_taxes_by_id = {}
        
        # Collect calculated tax amounts from tree data
        for tax_info in extended_taxes:
            tax_id = tax_info.get('tax_id', 'Unknown')
            recomputed_total = float(tax_info.get('recomputed_total', 0.0))
            calculated_taxes_by_id[tax_id] = recomputed_total
        
        # Collect orderTaxes from database
        for ot in order_taxes_section:
            tax_id = ot.get('_id')
            if isinstance(tax_id, dict) and '$oid' in tax_id:
                tax_id = tax_id['$oid']
            elif tax_id:
                tax_id = str(tax_id)
            if tax_id:
                # Try both 'amount' and 'taxAmount' field names
                order_tax_amount = float(ot.get('amount', ot.get('taxAmount', 0.0)))
                order_taxes_by_id[tax_id] = order_tax_amount
        
        # Compare calculated vs order taxes for each tax ID
        all_taxes_match = True
        per_tax_validation = []
        
        all_tax_ids_combined = set(calculated_taxes_by_id.keys()) | set(order_taxes_by_id.keys())
        
        for tax_id in all_tax_ids_combined:
            calculated_amount = calculated_taxes_by_id.get(tax_id, 0.0)
            order_amount = order_taxes_by_id.get(tax_id, 0.0)
            variance = calculated_amount - order_amount
            matches = abs(variance) < 1e-4
            if not matches:
                all_taxes_match = False
            
            # Get tax name for display
            tax_name = "Unknown"
            for tax_info in extended_taxes:
                if tax_info.get('tax_id') == tax_id:
                    tax_name = tax_info.get('tax_name', 'Unknown')
                    break
            
            per_tax_validation.append({
                'tax_id': tax_id,
                'tax_name': tax_name,
                'calculated': calculated_amount,
                'order_db': order_amount,
                'variance': variance,
                'matches': matches
            })
        
        # Overall status based on per-tax comparison
        if all_taxes_match:
            overall_status_colored = "\033[1;32mPASSED\033[0m"
        else:
            overall_status_colored = "\033[1;31mFAILED\033[0m"
        
        print(f"   Overall Status: {overall_status_colored}")
        print(f"   Taxes Validated: {len(per_tax_validation)}")
        print("")
        
        # Show per-tax comparison table
        print("   Per-Tax Validation:")
        print("   " + "-" * 80)
        header = f"   {'Tax Name':<12} {'Tax ID':<26} {'Calculated':>12} {'Order DB':>12} {'Variance':>12} {'Status':<8}"
        print(header)
        print("   " + "-" * 80)
        
        for tax_validation in per_tax_validation:
            tax_name = tax_validation['tax_name'][:11]
            tax_id_short = tax_validation['tax_id'][:25]
            calculated = tax_validation['calculated']
            order_db = tax_validation['order_db']
            variance = tax_validation['variance']
            status_icon = "✅ PASS" if tax_validation['matches'] else "❌ FAIL"
            
            print(f"   {tax_name:<12} {tax_id_short:<26} {calculated:>12.5f} {order_db:>12.5f} {variance:>12.5f} {status_icon}")
        
        print("")
        print(f"   Total Calculated Tax Amount: ${sum(calculated_taxes_by_id.values()):.5f}")
        print(f"   Total Order DB Tax Amount:   ${sum(order_taxes_by_id.values()):.5f}")
        total_variance_new = sum(calculated_taxes_by_id.values()) - sum(order_taxes_by_id.values())
        print(f"   Total Variance (Calculated - Order DB): ${total_variance_new:.5f}")

    # ------------------------------------------------------------------
    # TAX SUMMARY (Aggregated view)
    # ------------------------------------------------------------------
    payment_tax_amount = summary.get('payment_tax_amount')
    tax_recon = summary.get('tax_reconciliation') or []
    show_aggregated = tax_view in ("full", "failures")
    if tax_recon and show_aggregated:
        # Only show per-tax reconciliation table (no overall payment summary per latest request)
        anomalies = summary.get('tax_anomalies') or {}
        miss = anomalies.get('missing_in_order') or []
        order_only = anomalies.get('order_only') or []
        if miss or order_only:
            print("\nTAX ANOMALIES:")
            print("-" * 40)
            if miss:
                print(f"   ⚠️ Missing in orderTaxes: {', '.join(miss)}")
            if order_only:
                print(f"   ⚠️ Order-only taxes: {', '.join(order_only)}")

        print("\nPER-TAX RECONCILIATION:")
        print("-" * 50)
        header = f"{'TaxId':<10} {'Rate%':>6} {'Menu':>10} {'Charges':>10} {'Combined':>10} {'Order':>10} {'ΔMenu-Order':>13} {'ΔComb-Order':>13}"
        print("   " + header)
        print("   " + "-" * len(header))
        rows = tax_recon
        if tax_view == "failures":
            rows = [r for r in tax_recon if not r.get('flags',{}).get('combined_order_match')]
            if not rows:
                print("   ✅ No per-tax variances. (failures view)")
        for row in rows:
            tid = row.get('tax_id', '')
            rate = row.get('rate', 0.0)
            menu_tax = row.get('menu_tax', 0.0)
            charges_tax = row.get('charges_tax', 0.0)
            combined = row.get('combined_menu_charges', 0.0)
            order_tax = row.get('order_tax', 0.0)
            v_menu_order = row.get('variance_menu_vs_order', 0.0)
            v_comb_order = row.get('variance_combined_vs_order', 0.0)
            flags = row.get('flags', {})
            icon = "✅" if flags.get('combined_order_match') else ("⚠️" if flags.get('menu_order_match') else "❌")
            print(f"   {icon} {tid[:8]:<10} {rate:>6.2f} {menu_tax:>10.5f} {charges_tax:>10.5f} {combined:>10.5f} {order_tax:>10.5f} {v_menu_order:>13.5f} {v_comb_order:>13.5f}")

    # Detailed per-tax itemized breakdown removed per latest requirement.

    # --------------------------------------------------------------
    # FINAL SUMMARY OF THE ORDER
    # --------------------------------------------------------------
    if charges_validation:
        print("\nSUMMARY OF THE ORDER:")
        print("=" * 60)
        status = _status_label(charges_validation.get('is_valid'))
        print(f"   Overall Status: {status}")
        print(f"   Expected Total Price: {charges_validation.get('expected_total_price'):.5f}")
        print(f"   Stored Total Price:   {charges_validation.get('stored_total_price'):.5f} (Match: {'YES' if charges_validation.get('total_price_match') else 'NO'})")
        print(f"   Sub Total:            {charges_validation.get('unit_price'):.5f}")
        print(f"   Discount Amount:      {charges_validation.get('discount_amount'):.5f}")
        print(f"   Included Charges Cnt: {charges_validation.get('included_charge_count')}")
        print(f"   Included Charges Sum: {charges_validation.get('included_charges_total'):.5f}")
        print(f"   Stored Tax Amount:    {charges_validation.get('stored_tax_amount'):.5f}")
        print(f"   Sum orderTaxes:       {charges_validation.get('sum_order_taxes'):.5f} (Match: {'YES' if charges_validation.get('tax_total_match') else 'NO'})")

def main(args: Optional[list] = None) -> int:
    """
    Main entry point for the CLI.
    
    Args:
        args: Command line arguments (defaults to sys.argv[1:])
        
    Returns:
        Exit code
    """
    parser = create_parser()
    parsed_args = parser.parse_args(args)
    
    # Set up logging
    log_level = "DEBUG" if parsed_args.verbose else "INFO"
    logger = setup_logging(level=log_level, log_file=parsed_args.log_file)
    
    try:
        if parsed_args.command == "verify-order":
            verify_order_tax(
                order_id=parsed_args.order_id,
                environment=parsed_args.env,
                tax_view=getattr(parsed_args, 'tax_view', 'basic'),
                precision=getattr(parsed_args, 'precision', 5)
            )
            
        elif not parsed_args.command:
            parser.print_help()
            return 1
            
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

