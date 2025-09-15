"""
Command-line interface for Smart Cal.
"""

import argparse
import sys
from typing import Optional

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
    
    return parser


def verify_order_tax(order_id: str, environment: str = "staging") -> None:
    """
    Verify tax calculations for a MongoDB order.
    
    Args:
        order_id: Internal ID of the order to verify
        environment: Database environment ("staging", "production", "stg", "prod")
    """
    logger = setup_logging()
    logger.info(f"Verifying tax for order: {order_id} in {environment} environment")
    
    # Map environment aliases
    env_map = {
        "staging": "stg",
        "stg": "stg", 
        "production": "prod",
        "prod": "prod"
    }
    env_key = env_map.get(environment.lower(), "stg")
    
    # Database name mapping
    db_names = {
        "stg": "GRUBTECH_MASTER_DATA_STG_V2",
        "prod": "GRUBTECH_MASTER_DATA_PROD_V2"
    }
    db_name = db_names[env_key]
    
    print(f"🔍 Environment: {environment.upper()}")
    print(f"📊 Database: {db_name}")
    print(f"🆔 Order ID: {order_id}")
    print("=" * 50)
    
    try:
        verification_service = TaxVerificationService(db_name=db_name)
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
    
    print(f"\nSMART CAL - TAX VERIFICATION ANALYSIS")
    print(f"=" * 45)
    print(f"Order Reference: {order_id}")
    
    # Calculate total tax amount across all tax rates
    total_expected_tax = sum(tax.get('expected_total', 0.0) for tax in result.get('taxes', []))
    total_recomputed_tax = sum(tax.get('recomputed_total', 0.0) for tax in result.get('taxes', []))
    total_difference = total_recomputed_tax - total_expected_tax
    
    print(f"Total Tax Amount (All Rates): ${total_expected_tax:.5f}")
    print(f"Total Recomputed Tax:         ${total_recomputed_tax:.5f}")
    
    # Show verification status
    if abs(total_difference) < 0.00001:
        print(f"Verification Status: \033[1mPASSED\033[0m - Tax calculations are accurate!")
    else:
        print(f"Verification Status: \033[1mFAILED\033[0m - Difference: ${total_difference:.5f}")
    
    # Show discount pattern information
    summary = result.get('summary', {})
    if 'pattern_info' in summary:
        pattern_info = summary['pattern_info']
        print(f"\nDiscount Pattern: {pattern_info.get('pattern', 'Unknown')}")
        if pattern_info.get('item_discounts', 0) > 0:
            print(f"Item-Level Discounts:   ${pattern_info['item_discounts']:.5f}")
        if pattern_info.get('order_discount', 0) > 0:
            print(f"Order-Level Discount:   ${pattern_info['order_discount']:.5f}")
        if pattern_info.get('remaining_order_discount', 0) > 0:
            print(f"Remaining Order Disc:   ${pattern_info['remaining_order_discount']:.5f}")
    
    print(f"\nTAX BREAKDOWN BY RATE:")
    print(f"-" * 40)
    
    for tax_result in result.get('taxes', []):
        tax_id = tax_result.get('tax_id')
        tax_name = tax_result.get('tax_name', 'Unknown')
        tax_rate = tax_result.get('tax_rate', 0.0)
        expected = tax_result.get('expected_total', 0.0)
        recomputed = tax_result.get('recomputed_total', 0.0)
        difference = tax_result.get('difference', 0.0)
        
        print(f"\nTax Category: {tax_id[:8]}... (Rate: {tax_rate:.1f}%)")
        print(f"   Expected (Database):    ${expected:.5f}")
        print(f"   Recomputed (Calculated): ${recomputed:.5f}")
        
        if abs(difference) < 0.00001:
            print(f"   Variance:               ${difference:.5f} (Perfect Match!)")
        elif difference > 0:
            print(f"   Variance:               ${difference:.5f} (Over-calculated)")
        else:
            print(f"   Variance:               ${difference:.5f} (Under-calculated)")
        print()
        
        print(f"ITEMIZED TAX BREAKDOWN:")
        print(f"   " + "-" * 35)
        
        # Display items with their modifiers grouped together
        for item in tax_result.get('details', {}).get('items', []):
            print(f"   ITEM: {item['name']} (Quantity: {item['qty']})")
            print(f"      Unit Price:             ${item.get('unit_price', 0.0):.5f}")
            print(f"      Item Total Price:       ${item['total_price']:.5f}")
            
            # Show item-level discount if present
            if item.get('item_discount', 0.0) > 0:
                print(f"      Item Discount Applied:  ${item['item_discount']:.5f}")
            
            # Show distributed order discount if present  
            if item.get('distributed_order_discount', 0.0) > 0:
                print(f"      Order Discount Share:   ${item['distributed_order_discount']:.5f}")
            elif item.get('distributed_discount', 0.0) > 0:
                print(f"      Distributed Discount:   ${item['distributed_discount']:.5f}")
            
            # Show taxable amount
            if 'taxable_amount' in item:
                print(f"      Taxable Amount:         ${item['taxable_amount']:.5f}")
            elif 'tax_inclusive_amount' in item:
                print(f"      Tax-Inclusive Amount:   ${item['tax_inclusive_amount']:.5f}")
                
            print(f"      Expected Tax (DB):      ${item['expected']:g}")  # Use :g to show exact value without trailing zeros
            print(f"      Recomputed Tax:         ${item['recomputed']:.5f}")
            
            # Enhanced difference display
            diff = item['difference']
            if abs(diff) < 0.00001:
                print(f"      Tax Variance:           ${diff:.5f} (Perfect!)")
            elif diff > 0:
                print(f"      Tax Variance:           ${diff:.5f} (Over-estimated)")
            else:
                print(f"      Tax Variance:           ${diff:.5f} (Under-estimated)")
            
            # Display modifiers for this specific item
            item_modifiers = [mod for mod in tax_result.get('details', {}).get('modifiers', []) 
                            if mod['parent_item'] == item['name']]
            
            if item_modifiers:
                print(f"      Add-ons & Modifications:")
                for mod in item_modifiers:
                    # Determine if modifier has tax or is tax-free
                    has_tax = mod.get('expected', 0.0) > 0 or mod.get('total_price', 0.0) > 0
                    tax_status = "[TAXABLE]" if has_tax else "[TAX-FREE]"
                    
                    print(f"         {tax_status} {mod['name']} (Applied {mod['parent_qty']}x)")
                    print(f"            Unit Price:             ${mod.get('unit_price', 0.0):.5f}")
                    print(f"            Modifier Total Price:   ${mod['total_price']:.5f}")
                    
                    # Show modifier-level discount if present
                    if mod.get('modifier_discount', 0.0) > 0:
                        print(f"            Modifier Discount:      ${mod['modifier_discount']:.5f}")
                    
                    # Show distributed order discount if present
                    if mod.get('distributed_order_discount', 0.0) > 0:
                        print(f"            Order Discount Share:   ${mod['distributed_order_discount']:.5f}")
                    elif mod.get('distributed_discount', 0.0) > 0:
                        print(f"            Distributed Discount:   ${mod['distributed_discount']:.5f}")
                    
                    # Show taxable amount
                    if 'taxable_amount' in mod:
                        print(f"            Taxable Amount:         ${mod['taxable_amount']:.5f}")
                    elif 'tax_inclusive_amount' in mod:
                        print(f"            Tax-Inclusive Amount:   ${mod['tax_inclusive_amount']:.5f}")
                    
                    # Show tax calculations
                    print(f"            Expected Tax (DB):      ${mod['expected']:g}")  # Use :g to show exact value without trailing zeros
                    print(f"            Recomputed (Base):      ${mod['recomputed_base']:.5f}")
                    print(f"            Recomputed (Final):     ${mod['recomputed_final']:.5f}")
                    
                    # Enhanced difference display
                    diff = mod['difference']
                    if abs(diff) < 0.00001:
                        print(f"            Tax Variance:           ${diff:.5f} (Perfect!)")
                    elif diff > 0:
                        print(f"            Tax Variance:           ${diff:.5f} (Over-estimated)")
                    else:
                        print(f"            Tax Variance:           ${diff:.5f} (Under-estimated)")
                    print()
            print()


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
            verify_order_tax(order_id=parsed_args.order_id, environment=parsed_args.env)
            
        elif not parsed_args.command:
            parser.print_help()
            return 1
            
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())

