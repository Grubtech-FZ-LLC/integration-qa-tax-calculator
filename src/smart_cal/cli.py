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
    
    return parser


def verify_order_tax(order_id: str, environment: str = "staging", tax_view: str = "basic") -> None:
    """
    Verify tax calculations for a MongoDB order.
    
    Args:
        order_id: Internal ID of the order to verify
        environment: Database environment ("staging", "production", "stg", "prod")
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
        # Pass environment-specific connection URL
        verification_service = TaxVerificationService(
            db_name=db_name,
            connection_url_env_key=config["connection_url"]
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
    # Divider after box
    # print("=" * (inner_width))  # optional
    # Show discount pattern information
    if 'pattern_info' in summary:
        pattern_info = summary['pattern_info']
        
        # Show discount pattern - without mentioning correction to avoid confusion
        print(f"\nDiscount Pattern: {pattern_info.get('pattern', 'Unknown')}")
            
        # Show discount amounts
        if pattern_info.get('item_discounts', 0) > 0:
            print(f"Item-Level Discounts:   ${pattern_info['item_discounts']:.5f}")
        if pattern_info.get('order_discount', 0) > 0:
            print(f"Order-Level Discount:   ${pattern_info['order_discount']:.5f}")
        if pattern_info.get('remaining_order_discount', 0) > 0:
            print(f"Remaining Order Disc:   ${pattern_info['remaining_order_discount']:.5f}")
            
        # Display discount validation warnings if present
        if 'discount_valid' in pattern_info and not pattern_info['discount_valid']:
            print(f"\n\033[1m\033[33m⚠️  DISCOUNT VALIDATION WARNING ⚠️\033[0m")
            if pattern_info.get('discount_warning'):
                print(f"\033[33m{pattern_info['discount_warning']}\033[0m")
            print(f"\033[33mRecommendation: Check the original order payload and discount application logic.\033[0m")
    
    # New: menuDetails price calculations validation section
    menu_validation = summary.get('menu_calculations_validation')
    if menu_validation:
        print("\nMENU DETAILS PRICE CALCULATIONS VALIDATION:")
        print("=" * 55)
        
        total_items = menu_validation.get('total_items', 0)
        is_valid = menu_validation.get('is_valid', False)
        calculation_errors = menu_validation.get('calculation_errors', [])
        validation_details = menu_validation.get('validation_details', [])
        status_colored = "\033[1;32mPASS\033[0m" if is_valid else "\033[1;31mFAIL\033[0m"
        print(f"   Overall Calculation Status: {status_colored}")
        print(f"   Items Validated: {total_items}")
        
        if calculation_errors:
            print(f"   Calculation Errors Found: {len(calculation_errors)}")
        
        # Show detailed calculation validation for each item
        if validation_details:
            print(f"\n   DETAILED CALCULATION VALIDATION:")
            print(f"   " + "-" * 50)
            
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
                    print(f"{prefix}{icon} {fname:<{field_col_width-2}}{actual:>14.5f}{expected:>14.5f}{delta:>16.8f}")
                    if not is_calc_valid and formula:
                        # Indent formula under the row (align under first data column)
                        print(f"{prefix}{'':2}Formula: {formula}")

            for item_validation in validation_details:
                item_name = item_validation.get('item_name', 'Unknown Item')
                item_id = item_validation.get('item_id', 'Unknown ID')
                qty = item_validation.get('qty', 1)
                tax_rate = item_validation.get('tax_rate', 0)

                print(f"\n   📦 Item: {item_name} (ID: {item_id})")
                print(f"      Qty: {qty}, Tax Rate: {tax_rate:.2f}%")
                _print_calc_table("      ", item_validation.get('calculations', {}))

                # Modifiers section
                modifiers = item_validation.get('modifiers', [])
                for modifier_validation in modifiers:
                    mod_name = modifier_validation.get('item_name', 'Unknown Modifier')
                    mod_id = modifier_validation.get('item_id', 'Unknown ID')
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
    # ANSI color helper for PASS/FAIL
    def _status_label(ok: bool) -> str:
        return "\033[1;32mPASS\033[0m" if ok else "\033[1;31mFAIL\033[0m"
    if consistency:
        print("\nMENU / ITEM DETAILS CONSISTENCY:")
        print("=" * 50)
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
                print(f"\n   DETAILED FIELD COMPARISON:")
                print(f"   " + "-" * 47)
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

    # Charges validation section
    charges_validation = summary.get('charges_validation')
    if charges_validation:
        print("\nORDER CHARGES VALIDATION:")
        print("=" * 50)
        # Core summary lines moved to final SUMMARY OF THE ORDER section
        charge_entries = charges_validation.get('charge_entries', [])
        included_count = charges_validation.get('included_charge_count')
        # Show N/A if there are no included charges and no detailed entries to render
        if (included_count in (0, None) or included_count == 0) and not charge_entries:
            print("   Status: \033[36mN/A\033[0m (no invoice-included charges)")
        else:
            status = _status_label(charges_validation.get('is_valid'))
            print(f"   Status: {status} (detailed per-charge integrity below)")

        # Charge entries (tabular improved readability)
        if charge_entries:
            print("\n   CHARGES DETAIL:")
            headers = [
                ("Type", 14),
                ("Incl", 5),
                ("Amount", 12),
                ("TaxExcl", 12),
                ("Tax", 10),
                ("SumTaxes", 12),
                ("Rate%", 8),
                ("RecompTax", 12),
                ("Match", 7),
                ("Internal", 9),
            ]
            header_line = " ".join([f"{h:<{w}}" for h, w in headers])
            print(f"      {header_line}")
            print(f"      {'-' * len(header_line)}")

            for ch in charge_entries:
                ch_type = str(ch.get('type'))[:14]
                incl = 'Y' if ch.get('includeInInvoice') else 'N'
                amount = f"{ch.get('amount',0.0):.5f}"
                tax_excl = f"{ch.get('taxExclusiveAmount',0.0):.5f}"
                tax_val = f"{ch.get('tax',0.0):.5f}"
                sum_taxes = f"{ch.get('sum_list_tax',0.0):.5f}"
                applied_rate = ch.get('applied_rate')
                rate_str = f"{applied_rate:.2f}" if applied_rate is not None else "-"
                recomputed_tax = ch.get('recomputed_tax')
                recomp_str = f"{recomputed_tax:.5f}" if recomputed_tax is not None else "-"
                match_icon = '✅' if ch.get('recomputed_tax_match') else ('❌' if recomputed_tax is not None else '-')
                internal_icon = '✅' if ch.get('internal_ok') else '❌'
                row_parts = [
                    f"{ch_type:<14}",
                    f"{incl:<5}",
                    f"{amount:>12}",
                    f"{tax_excl:>12}",
                    f"{tax_val:>10}",
                    f"{sum_taxes:>12}",
                    f"{rate_str:>8}",
                    f"{recomp_str:>12}",
                    f"{match_icon:>7}",
                    f"{internal_icon:>9}",
                ]
                print("      " + " ".join(row_parts))

                # Taxes line (single line listing taxId:amount pairs)
                taxes_list = ch.get('taxes', [])
                if taxes_list:
                    tax_pairs = [f"{t.get('taxId','?')[-6:]}:{t.get('amount',0.0):.5f}" for t in taxes_list]
                    print(f"           ↳ Taxes: {' | '.join(tax_pairs)}")

        # (Removed CHARGE TAX vs ORDER TAX MAPPING section per user request)

        errors = charges_validation.get('errors', [])
        if errors:
            print("\n   ERRORS:")
            for e in errors:
                print(f"      - {e.get('issue')}: {e.get('message')}")

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
        print("=" * 50)
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
                tax_view=getattr(parsed_args, 'tax_view', 'basic')
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

