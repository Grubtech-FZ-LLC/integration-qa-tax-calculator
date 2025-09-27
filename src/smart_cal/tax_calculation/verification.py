"""Order tax verification service (simplified from legacy implementation)."""

from __future__ import annotations

from typing import Any, Dict, List

from .repository import OrderRepository


class TaxVerificationService:
    """High-level service for tax verification operations."""
    
    def __init__(self, db_name: str = None, connection_url_env_key: str = None):
        self.verifier = OrderTaxVerifier()
        self.db_name = db_name
        self.connection_url_env_key = connection_url_env_key
        
    def verify_order_by_id(self, order_id: str) -> Dict[str, Any]:
        """Verify tax calculations for an order by its internal ID."""
        with OrderRepository(
            db_name=self.db_name, 
            connection_url_env_key=self.connection_url_env_key
        ) as repo:
            order_data = repo.get_order_by_internal_id(order_id)
            if not order_data:
                raise ValueError(f"Order with ID {order_id} not found")
            
            result = self.verifier.verify(order_data)
            
            # Transform result to expected format
            taxes = []
            for comp in result.get('comparisons', []):
                taxes.append({
                    'tax_id': comp['tax_id'],
                    'tax_name': f"Tax {comp['tax_id']}",  # Could be enhanced
                    'tax_rate': comp['rate'],  # Include the tax rate
                    'expected_total': comp['menu_sum'],
                    'recomputed_total': comp['recomputed'],
                    'difference': comp['menu_recomputed_diff'],
                    'details': comp['details']
                })
            
            return {
                'order_id': order_id,
                'order_amount': sum(comp['order_amount'] for comp in result.get('comparisons', [])),
                'taxes': taxes,
                'summary': result.get('summary', {})
            }


class OrderTaxVerifier:
    """Compare calculated menu taxes with stored order taxes.

    This is a simplified verifier that:
    - Iterates menuDetails (items and modifiers)
    - Sums expected tax amounts by taxId
    - Recomputes tax using BasicTaxCalculator based on totalPrice and rate
    - Returns per-taxId comparison and a summary
    """

    def __init__(self) -> None:
        pass

    def _get_order_level_discount(self, order_data: Dict[str, Any]) -> float:
        """Extract order-level discount from paymentDetails.priceDetails.discountAmount."""
        payment_details = order_data.get("paymentDetails", {})
        price_details = payment_details.get("priceDetails", {})
        return float(price_details.get("discountAmount", 0.0))

    def _get_item_level_discount(self, item: Dict[str, Any]) -> float:
        """Extract item-level discount from item.price.discountAmount.
        
        Note: In the DB structure, totalPrice is already discounted (unitPrice - discountAmount).
        This method returns the discount for display purposes only.
        """
        return float(item.get("price", {}).get("discountAmount", 0.0))

    def _get_modifier_level_discount(self, modifier: Dict[str, Any]) -> float:
        """Extract modifier-level discount from modifier.price.discountAmount.
        
        Note: In the DB structure, totalPrice is already discounted (unitPrice - discountAmount).
        This method returns the discount for display purposes only.
        """
        return float(modifier.get("price", {}).get("discountAmount", 0.0))

    def _has_item_level_discounts(self, order_data: Dict[str, Any]) -> bool:
        """Check if order has any item-level or modifier-level discounts."""
        for item in order_data.get("menuDetails", []):
            if self._get_item_level_discount(item) > 0:
                return True
            for mod in item.get("extraDetails", []):
                if self._get_modifier_level_discount(mod) > 0:
                    return True
        return False

    def _get_total_item_level_discounts(self, order_data: Dict[str, Any]) -> float:
        """Calculate total of all item-level and modifier-level discounts."""
        total = 0.0
        for item in order_data.get("menuDetails", []):
            total += self._get_item_level_discount(item)
            for mod in item.get("extraDetails", []):
                # Modifier discounts are multiplied by parent item quantity
                total += self._get_modifier_level_discount(mod) * int(item.get("qty", 1))
        return total

    def _get_calculated_subtotal(self, order_data: Dict[str, Any]) -> float:
        """Calculate total subtotal for order-level discount distribution.
        
        First try to use paymentDetails.priceDetails.unitPrice (pre-discount total),
        otherwise sum all menuDetails items/modifiers.
        """
        # Try to get pre-discount total from payment details
        payment_details = order_data.get("paymentDetails", {})
        price_details = payment_details.get("priceDetails", {})
        unit_price = price_details.get("unitPrice")
        
        if unit_price is not None and unit_price > 0:
            return float(unit_price)
        
        # Fallback: calculate from menuDetails
        total = 0.0
        for item in order_data.get("menuDetails", []):
            total += float(item.get("price", {}).get("totalPrice", 0.0))
            # Include all modifiers in subtotal calculation
            for mod in item.get("extraDetails", []):
                total += float(mod.get("price", {}).get("totalPrice", 0.0))
        return total

    def _get_tax_rate_by_id(self, order_data: Dict[str, Any], tax_id: str) -> float:
        """
        Get tax rate by ID from orderTaxes, but only for tax IDs that exist in menuDetails.
        
        Process:
        1. Collect tax IDs from menuDetails[i].taxes[j].taxId and menuDetails[i].extraDetails[x].taxes[y].taxId
        2. Get rate from orderTaxes[] where _id matches the collected tax ID
        """
        
        # Step 1: Collect all tax IDs from menuDetails (items and modifiers)
        menu_tax_ids = set()
        
        for item in order_data.get("menuDetails", []):
            # Collect tax IDs from item level
            for tax_info in item.get("taxes", []):
                if "taxId" in tax_info:
                    menu_tax_ids.add(str(tax_info["taxId"]))
            
            # Collect tax IDs from modifier level (extraDetails)
            for modifier in item.get("extraDetails", []):
                for tax_info in modifier.get("taxes", []):
                    if "taxId" in tax_info:
                        menu_tax_ids.add(str(tax_info["taxId"]))
        
        # Step 2: Check if the requested tax_id exists in menuDetails
        if str(tax_id) not in menu_tax_ids:
            return 0.0
        
        # Step 3: Get rate from orderTaxes where _id matches the tax_id
        for order_tax in order_data.get("orderTaxes", []):
            order_tax_id = str(order_tax.get("_id", order_tax.get("taxId", "")))
            if order_tax_id == str(tax_id):
                return float(order_tax.get("rate", order_tax.get("taxRate", 0.0)))
        
        # If rate not found in orderTaxes, return 0
        return 0.0

    def _sum_expected_menu_tax(self, order_data: Dict[str, Any], tax_id: str) -> float:
        total = 0.0
        for item in order_data.get("menuDetails", []):
            for t in item.get("taxes", []):
                if str(t.get("taxId")) == str(tax_id):
                    total += float(t.get("amount", 0.0))
            for mod in item.get("extraDetails", []):
                for t in mod.get("taxes", []):
                    if str(t.get("taxId")) == str(tax_id):
                        # Multiply by parent quantity if present in data
                        total += float(t.get("amount", 0.0)) * int(item.get("qty", 1))
        return total  # Return exact DB value without rounding

    def _recompute_menu_tax(self, order_data: Dict[str, Any], tax_id: str) -> float:
        rate = self._get_tax_rate_by_id(order_data, tax_id)
        if rate <= 0:
            return 0.0
        
        # Check discount types present
        has_item_discounts = self._has_item_level_discounts(order_data)
        order_discount = self._get_order_level_discount(order_data)
        total_item_discounts = self._get_total_item_level_discounts(order_data)
        
        # Pattern determination:
        # Pattern 1: No discounts at all
        # Pattern 2: Only order-level discount
        # Pattern 3: Only item-level discounts 
        # Pattern 4: Both item-level AND order-level discounts (combination)
        if has_item_discounts and order_discount > 0:
            # Pattern 4: Combined discounts
            # First apply item-level discounts, then distribute remaining order discount
            # remaining_order_discount = total_order_discount - total_item_level_discounts
            remaining_order_discount = max(0.0, order_discount - total_item_discounts)
        elif has_item_discounts:
            # Pattern 3: Item-level discounts only
            remaining_order_discount = 0.0
        else:
            # Pattern 2: Order-level discount distribution (or Pattern 1 if order_discount = 0)
            remaining_order_discount = order_discount
        
        total = 0.0
        for item in order_data.get("menuDetails", []):
            # include item only if it has this tax_id
            item_has_tax = any(str(t.get("taxId")) == str(tax_id) for t in item.get("taxes", []))
            if item_has_tax:
                price = float(item.get("price", {}).get("totalPrice", 0.0))  # Already includes item discount
                item_discount = self._get_item_level_discount(item)  # For display only
                
                # Pattern 4: Item discounts already applied to totalPrice, now apply remaining order discount
                if has_item_discounts and remaining_order_discount > 0:
                    # Pattern 4: Apply additional order-level discount distribution
                    # Formula: tax inclusive sub total with item level discount (a) = paymentDetails.priceDetails.unitPrice - total item level discount
                    payment_details = order_data.get("paymentDetails", {})
                    price_details = payment_details.get("priceDetails", {})
                    unit_price = float(price_details.get("unitPrice", 0.0))
                    
                    tax_inclusive_subtotal_with_item_discount = unit_price - total_item_discounts
                    
                    if tax_inclusive_subtotal_with_item_discount > 0:
                        # distributed taxable discount amount on item (b) = (order level discount / a) x item.totalPrice
                        distributed_order_discount = (remaining_order_discount / tax_inclusive_subtotal_with_item_discount) * price
                        # tax inclusive amount on item with discount (c) = item.totalPrice - b
                        taxable_amount = price - distributed_order_discount
                    else:
                        taxable_amount = price
                elif has_item_discounts and remaining_order_discount == 0:
                    # Pattern 3: totalPrice is already post-discount
                    taxable_amount = price
                else:
                    # Pattern 2: Apply order-level discount distribution
                    if remaining_order_discount > 0:
                        calculated_subtotal = self._get_calculated_subtotal(order_data)
                        if calculated_subtotal > 0:
                            distributed_order_discount = (remaining_order_discount / calculated_subtotal) * price
                            taxable_amount = price - distributed_order_discount
                        else:
                            taxable_amount = price
                    else:
                        taxable_amount = price
                    
                total += self._inclusive_tax(taxable_amount, rate)
            
            # modifiers: include only those that have this tax_id; multiply by parent qty
            qty = int(item.get("qty", 1))
            for mod in item.get("extraDetails", []):
                mod_has_tax = any(str(t.get("taxId")) == str(tax_id) for t in mod.get("taxes", []))
                if not mod_has_tax:
                    continue
                mprice = float(mod.get("price", {}).get("totalPrice", 0.0))  # Already includes modifier discount
                mod_discount = self._get_modifier_level_discount(mod)  # For display only
                
                # Pattern 4: Modifier discounts already applied to totalPrice, now apply remaining order discount
                if has_item_discounts and remaining_order_discount > 0:
                    # Pattern 4: Apply additional order-level discount distribution
                    # Formula: tax inclusive sub total with item level discount (a) = paymentDetails.priceDetails.unitPrice - total item level discount
                    payment_details = order_data.get("paymentDetails", {})
                    price_details = payment_details.get("priceDetails", {})
                    unit_price = float(price_details.get("unitPrice", 0.0))
                    
                    tax_inclusive_subtotal_with_item_discount = unit_price - total_item_discounts
                    
                    if tax_inclusive_subtotal_with_item_discount > 0:
                        # distributed taxable discount amount on modifier (b) = (order level discount / a) x modifier.totalPrice  
                        distributed_order_discount = (remaining_order_discount / tax_inclusive_subtotal_with_item_discount) * mprice
                        # tax inclusive amount on modifier with discount (c) = modifier.totalPrice - b
                        mod_taxable_amount = mprice - distributed_order_discount
                    else:
                        mod_taxable_amount = mprice
                elif has_item_discounts and remaining_order_discount == 0:
                    # Pattern 3: totalPrice is already post-discount
                    mod_taxable_amount = mprice
                else:
                    # Pattern 2: Apply order-level discount distribution
                    if remaining_order_discount > 0:
                        calculated_subtotal = self._get_calculated_subtotal(order_data)
                        if calculated_subtotal > 0:
                            distributed_order_discount = (remaining_order_discount / calculated_subtotal) * mprice
                            mod_taxable_amount = mprice - distributed_order_discount
                        else:
                            mod_taxable_amount = mprice
                    else:
                        mod_taxable_amount = mprice
                    
                base = self._inclusive_tax(mod_taxable_amount, rate)
                total += base * qty
        return round(total, 5)

    def _build_details(self, order_data: Dict[str, Any], tax_id: str) -> Dict[str, Any]:
        rate = self._get_tax_rate_by_id(order_data, tax_id)
        
        # Check discount types present
        has_item_discounts = self._has_item_level_discounts(order_data)
        order_discount = self._get_order_level_discount(order_data)
        total_item_discounts = self._get_total_item_level_discounts(order_data)
        
        # Pattern determination:
        # Pattern 1: No discounts at all
        # Pattern 2: Only order-level discount
        # Pattern 3: Only item-level discounts 
        # Pattern 4: Both item-level AND order-level discounts (combination)
        if has_item_discounts and order_discount > 0:
            # Pattern 4: Combined discounts
            # First apply item-level discounts, then distribute remaining order discount
            remaining_order_discount = max(0.0, order_discount - total_item_discounts)
        elif has_item_discounts:
            # Pattern 3: Item-level discounts only
            remaining_order_discount = 0.0
        else:
            # Pattern 2: Order-level discount distribution (or Pattern 1 if order_discount = 0)
            remaining_order_discount = order_discount
        
        items: List[Dict[str, Any]] = []
        modifiers: List[Dict[str, Any]] = []
        recomputed_total = 0.0
        
        for item in order_data.get("menuDetails", []):
            qty = int(item.get("qty", 1))
            total_price = float(item.get("price", {}).get("totalPrice", 0.0))
            expected_item_tax = 0.0
            for t in item.get("taxes", []):
                if str(t.get("taxId")) == str(tax_id):
                    expected_item_tax += float(t.get("amount", 0.0))
                    
            # include item row only if it has this tax_id on the menu
            if expected_item_tax > 0:
                item_discount = self._get_item_level_discount(item)  # For display only
                
                # Pattern 4: Item discounts already applied to totalPrice, now apply remaining order discount
                if has_item_discounts and remaining_order_discount > 0:
                    # Pattern 4: Apply additional order-level discount distribution
                    # Formula: tax inclusive sub total with item level discount (a) = paymentDetails.priceDetails.unitPrice - total item level discount
                    payment_details = order_data.get("paymentDetails", {})
                    price_details = payment_details.get("priceDetails", {})
                    unit_price = float(price_details.get("unitPrice", 0.0))
                    
                    tax_inclusive_subtotal_with_item_discount = unit_price - total_item_discounts
                    
                    if tax_inclusive_subtotal_with_item_discount > 0:
                        # distributed taxable discount amount on item (b) = (order level discount / a) x item.totalPrice
                        distributed_order_discount = (remaining_order_discount / tax_inclusive_subtotal_with_item_discount) * total_price
                        # tax inclusive amount on item with discount (c) = item.totalPrice - b
                        taxable_amount = total_price - distributed_order_discount
                    else:
                        distributed_order_discount = 0.0
                        taxable_amount = total_price
                elif has_item_discounts and remaining_order_discount == 0:
                    # Pattern 3: totalPrice is already post-discount
                    distributed_order_discount = 0.0
                    taxable_amount = total_price
                else:
                    # Pattern 2: Apply order-level discount distribution
                    if remaining_order_discount > 0:
                        calculated_subtotal = self._get_calculated_subtotal(order_data)
                        if calculated_subtotal > 0:
                            distributed_order_discount = (remaining_order_discount / calculated_subtotal) * total_price
                            taxable_amount = total_price - distributed_order_discount
                        else:
                            distributed_order_discount = 0.0
                            taxable_amount = total_price
                    else:
                        distributed_order_discount = 0.0
                        taxable_amount = total_price
                    
                recomputed_item_tax = self._inclusive_tax(taxable_amount, rate)
                recomputed_total += recomputed_item_tax
                
                items.append({
                    "name": item.get("name", "Unknown"),
                    "qty": qty,
                    "unit_price": round(float(item.get("price", {}).get("unitPrice", 0.0)), 5),
                    "total_price": round(total_price, 5),
                    "item_discount": round(item_discount, 5),
                    "distributed_order_discount": round(distributed_order_discount, 5),
                    "taxable_amount": round(taxable_amount, 5),
                    "expected": expected_item_tax,  # Show exact DB value without rounding
                    "recomputed": round(recomputed_item_tax, 5),
                    "difference": round(expected_item_tax - recomputed_item_tax, 5),
                })

            # modifiers - include ALL modifiers for this item, not just those with tax
            for mod in item.get("extraDetails", []):
                m_total = float(mod.get("price", {}).get("totalPrice", 0.0))
                expected_mod_tax = 0.0
                for t in mod.get("taxes", []):
                    if str(t.get("taxId")) == str(tax_id):
                        expected_mod_tax += float(t.get("amount", 0.0))
                
                # Include ALL modifiers, regardless of tax amount or price
                mod_discount = self._get_modifier_level_discount(mod)  # For display only
                
                # Pattern 4: Modifier discounts already applied to totalPrice, now apply remaining order discount
                if has_item_discounts and remaining_order_discount > 0:
                    # Pattern 4: Apply additional order-level discount distribution
                    # Formula: tax inclusive sub total with item level discount (a) = paymentDetails.priceDetails.unitPrice - total item level discount
                    payment_details = order_data.get("paymentDetails", {})
                    price_details = payment_details.get("priceDetails", {})
                    unit_price = float(price_details.get("unitPrice", 0.0))
                    
                    tax_inclusive_subtotal_with_item_discount = unit_price - total_item_discounts
                    
                    if tax_inclusive_subtotal_with_item_discount > 0:
                        # distributed taxable discount amount on modifier (b) = (order level discount / a) x modifier.totalPrice
                        distributed_order_discount = (remaining_order_discount / tax_inclusive_subtotal_with_item_discount) * m_total
                        # tax inclusive amount on modifier with discount (c) = modifier.totalPrice - b
                        mod_taxable_amount = m_total - distributed_order_discount
                    else:
                        distributed_order_discount = 0.0
                        mod_taxable_amount = m_total
                elif has_item_discounts and remaining_order_discount == 0:
                    # Pattern 3: totalPrice is already post-discount
                    distributed_order_discount = 0.0
                    mod_taxable_amount = m_total
                else:
                    # Pattern 2: Apply order-level discount distribution
                    if remaining_order_discount > 0:
                        calculated_subtotal = self._get_calculated_subtotal(order_data)
                        if calculated_subtotal > 0:
                            distributed_order_discount = (remaining_order_discount / calculated_subtotal) * m_total
                            mod_taxable_amount = m_total - distributed_order_discount
                        else:
                            distributed_order_discount = 0.0
                            mod_taxable_amount = m_total
                    else:
                        distributed_order_discount = 0.0
                        mod_taxable_amount = m_total
                    
                base_tax = self._inclusive_tax(mod_taxable_amount, rate)
                final_tax = base_tax * qty
                
                # Only add to recomputed_total if there's actual expected tax
                if expected_mod_tax > 0:
                    recomputed_total += final_tax
                
                modifiers.append({
                    "name": mod.get("name", "Unknown"),
                    "parent_item": item.get("name", "Unknown"),
                    "parent_qty": qty,
                    "unit_price": round(float(mod.get("price", {}).get("unitPrice", 0.0)), 5),
                    "total_price": round(m_total, 5),
                    "modifier_discount": round(mod_discount, 5),
                    "distributed_order_discount": round(distributed_order_discount, 5),
                    "taxable_amount": round(mod_taxable_amount, 5),
                    "expected": expected_mod_tax,  # Show exact DB value without rounding
                    "recomputed_base": round(base_tax, 5),
                    "recomputed_final": round(final_tax, 5),
                    "difference": round(expected_mod_tax - base_tax, 5),
                })

        return {"items": items, "modifiers": modifiers, "recomputed_total": round(recomputed_total, 5)}

    def _inclusive_tax(self, total_price: float, rate_percent: float) -> float:
        # Same as Basic approach but explicit for inclusivity
        if rate_percent <= 0 or total_price <= 0:
            return 0.0
        rate = rate_percent / 100.0
        tax = total_price - (total_price / (1.0 + rate))
        return round(tax, 5)

    def _validate_discount_consistency(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validate whether the discount pattern is consistent between item and order levels.
        
        Returns a dictionary with:
        - is_valid: Boolean indicating if discounts are consistent
        - pattern: The determined discount pattern (corrected if needed)
        - warning: Warning message if inconsistent
        - item_discounts: Total item-level discounts
        - order_discount: Order-level discount
        - corrected_pattern: True if pattern was corrected from the original determination
        """
        has_item_discounts = self._has_item_level_discounts(order_data)
        order_discount = self._get_order_level_discount(order_data)
        item_discounts = self._get_total_item_level_discounts(order_data)
        
        # Initial pattern determination
        if has_item_discounts and order_discount > 0:
            initial_pattern = "Pattern 4: Combined (Item + Order Level Discounts)"
        elif has_item_discounts:
            initial_pattern = "Pattern 3: Item-Level Discounts Only"
        elif order_discount > 0:
            initial_pattern = "Pattern 2: Order-Level Discount Only"
        else:
            initial_pattern = "Pattern 1: No Discounts"
            
        # Start with pattern matching the initial determination
        pattern = initial_pattern
        is_valid = True
        warning = None
        corrected_pattern = False
        
        # CASE 1: Detect when Pattern 4 should actually be Pattern 3
        # If order discount is suspiciously close to item discounts, it's likely a duplicated Pattern 3
        if initial_pattern == "Pattern 4: Combined (Item + Order Level Discounts)":
            # If order_discount is approximately equal to item_discounts (within 5%)
            if abs(order_discount - item_discounts) / max(0.01, item_discounts) < 0.05:
                # This is actually correct for Pattern 3 - just reclassify without warning
                pattern = "Pattern 3: Item-Level Discounts Only"  # Correct the pattern
                corrected_pattern = True
                warning = (
                    f"NOTE: Reclassified from Pattern 4 to Pattern 3.\n"
                    f"Order discount (${order_discount:.5f}) equals item discounts (${item_discounts:.5f}), indicating item-level discounts only."
                )
        
        # CASE 2: Verify Pattern 3 is used correctly
        # For Pattern 3, order discount should be 0 or equal to sum of item discounts
        elif initial_pattern == "Pattern 3: Item-Level Discounts Only" and order_discount > 0.001:
            # If order discount equals item discounts, it's correct but redundant
            if abs(order_discount - item_discounts) < 0.01:
                warning = (
                    f"NOTE: Pattern 3 detected with order discount ({order_discount:.5f}) equal to item discounts ({item_discounts:.5f}).\n"
                    f"This is technically correct but redundant - for cleaner data, consider setting order discount to 0."
                )
            # Otherwise, there's an inconsistency
            else:
                is_valid = False
                warning = (
                    f"WARNING: Item-Level Total Discounts Calculation Issue.\n" 
                    f"Item-level total discounts: ${item_discounts:.5f}, but paymentDetails.priceDetails.discountAmount: ${order_discount:.5f}.\n"
                    f"This may indicate an issue with the order payload or discount calculation logic."
                )
        
        return {
            "is_valid": is_valid,
            "pattern": pattern,
            "warning": warning,
            "item_discounts": item_discounts,
            "order_discount": order_discount,
            "corrected_pattern": corrected_pattern,
            "initial_pattern": initial_pattern
        }
        
    def _determine_pattern(self, order_data: Dict[str, Any]) -> str:
        """Determine which discount pattern applies to this order."""
        validation = self._validate_discount_consistency(order_data)
        return validation["pattern"]

    def verify(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        # Build the set of tax IDs that actually appear on menuDetails (items or modifiers)
        relevant_tax_ids = set()
        total_menu_items = 0
        total_modifiers = 0
        
        for item in order_data.get("menuDetails", []):
            total_menu_items += 1
            for t in item.get("taxes", []):
                if "taxId" in t:
                    relevant_tax_ids.add(str(t["taxId"]))
            for mod in item.get("extraDetails", []):
                total_modifiers += 1
                for t in mod.get("taxes", []):
                    if "taxId" in t:
                        relevant_tax_ids.add(str(t["taxId"]))

        # Check if no taxes are assigned to menu items
        if not relevant_tax_ids:
            raise ValueError(
                f"TAX ASSIGNMENT ERROR: This menu doesn't have any taxes assigned.\n"
                f"Found {total_menu_items} menu items and {total_modifiers} modifiers, "
                f"but all 'taxes' arrays are empty.\n"
                f"Please ensure tax categories are properly assigned to menu items before verification."
            )

        comparisons: List[Dict[str, Any]] = []

        # Iterate orderTaxes but only include those that are relevant to menuDetails
        for t in order_data.get("orderTaxes", []):
            tax_id = str(t.get("_id", t.get("taxId", "")))
            if tax_id not in relevant_tax_ids:
                # Skip order-level taxes that don't appear at menu level
                continue

            rate = float(t.get("rate", t.get("taxRate", 0.0)))
            # robust extraction of order-level tax amount
            order_amount = float(t.get("taxAmount", t.get("amount", t.get("value", 0.0))))
            # if order didn't specify an amount, consider using recomputed as proxy later

            menu_sum = self._sum_expected_menu_tax(order_data, tax_id)
            menu_vs_order_diff = round(menu_sum - order_amount, 5)

            # build per-item/modifier details and recompute reference from them
            details = self._build_details(order_data, tax_id)
            recomputed = details["recomputed_total"]
            if order_amount == 0.0 and recomputed > 0.0:
                # Use recomputed as order reference when missing in order data
                order_amount = recomputed
                menu_vs_order_diff = round(menu_sum - order_amount, 5)
            menu_vs_recomputed_diff = round(menu_sum - recomputed, 5)

            comparisons.append(
                {
                    "tax_id": tax_id,
                    "rate": rate,
                    "menu_sum": menu_sum,
                    "order_amount": order_amount,
                    "menu_order_diff": menu_vs_order_diff,
                    "recomputed": recomputed,
                    "menu_recomputed_diff": menu_vs_recomputed_diff,
                    "is_matching": abs(menu_vs_order_diff) < 1e-5,
                    "details": {
                        "items": details["items"],
                        "modifiers": details["modifiers"],
                    },
                }
            )

        # Validate discount consistency
        discount_validation = self._validate_discount_consistency(order_data)
        
        # Validate menuDetails price calculations
        menu_calculations_validation = self._validate_menu_details_calculations(order_data)
        
        # Compare menuDetails vs itemDetails consistency
        menu_item_consistency = self._compare_menu_and_item_details(order_data)
        # Validate charges
        charges_validation = self._validate_charges(order_data)

        summary = {
            "total_taxes": len(comparisons),
            "mismatches": sum(1 for c in comparisons if not c["is_matching"]),
            "total_difference": round(sum(c["menu_order_diff"] for c in comparisons), 5),
            "pattern_info": {
                "has_item_discounts": self._has_item_level_discounts(order_data),
                "order_discount": self._get_order_level_discount(order_data),
                "item_discounts": self._get_total_item_level_discounts(order_data),
                "remaining_order_discount": max(0.0, self._get_order_level_discount(order_data) - self._get_total_item_level_discounts(order_data)) if self._has_item_level_discounts(order_data) and self._get_order_level_discount(order_data) > 0 else (0.0 if self._has_item_level_discounts(order_data) else self._get_order_level_discount(order_data)),
                "pattern": discount_validation["pattern"],
                "initial_pattern": discount_validation.get("initial_pattern"),
                "corrected_pattern": discount_validation.get("corrected_pattern", False),
                "discount_valid": discount_validation["is_valid"],
                "discount_warning": discount_validation["warning"]
            },
            "menu_calculations_validation": menu_calculations_validation,
            "menu_item_consistency": menu_item_consistency,
            "charges_validation": charges_validation
        }

        # -----------------------------------------------------------------
        # Enhanced per-tax reconciliation data for CLI aggregated view
        # -----------------------------------------------------------------
        try:
            payment_tax_amount = 0.0
            payment_details = order_data.get("paymentDetails", {}) or {}
            price_details = payment_details.get("priceDetails", {}) or {}
            if price_details:
                payment_tax_amount = float(price_details.get("taxAmount", 0.0) or 0.0)

            # Maps
            menu_tax_by_id = {c["tax_id"]: round(c["menu_sum"], 5) for c in comparisons}
            recomputed_tax_by_id = {c["tax_id"]: round(c["recomputed"], 5) for c in comparisons}

            order_tax_by_id = {}
            for ot in order_data.get("orderTaxes", []) or []:
                raw_id = ot.get("_id") or ot.get("taxId")
                if raw_id is None:
                    continue
                oid = str(raw_id)
                amt = float(ot.get("amount", ot.get("taxAmount", ot.get("value", 0.0))) or 0.0)
                order_tax_by_id[oid] = round(amt, 5)

            charges_tax_by_id = {}
            if charges_validation and isinstance(charges_validation.get("charge_tax_by_id"), dict):
                charges_tax_by_id = dict(charges_validation.get("charge_tax_by_id"))

            all_tax_ids = set(menu_tax_by_id.keys()) | set(order_tax_by_id.keys()) | set(charges_tax_by_id.keys())
            tax_reconciliation: List[Dict[str, Any]] = []
            tolerance = 1e-5
            for tid in sorted(all_tax_ids):
                menu_val = menu_tax_by_id.get(tid, 0.0)
                order_val = order_tax_by_id.get(tid, 0.0)
                recomputed_val = recomputed_tax_by_id.get(tid, 0.0)
                charges_val = charges_tax_by_id.get(tid, 0.0)
                combined_val = round(menu_val + charges_val, 5)
                variance_menu_order = round(menu_val - order_val, 5)
                variance_combined_order = round(combined_val - order_val, 5)
                variance_menu_recomputed = round(menu_val - recomputed_val, 5)

                # Try to get rate from comparisons or orderTaxes
                rate = None
                for c in comparisons:
                    if c["tax_id"] == tid:
                        rate = c.get("rate")
                        break
                if rate is None:
                    # fallback from orderTaxes
                    for ot in order_data.get("orderTaxes", []) or []:
                        raw_id = ot.get("_id") or ot.get("taxId")
                        if str(raw_id) == tid:
                            rate = float(ot.get("rate", ot.get("taxRate", 0.0)) or 0.0)
                            break
                if rate is None:
                    rate = 0.0

                tax_reconciliation.append({
                    "tax_id": tid,
                    "rate": rate,
                    "menu_tax": menu_val,
                    "charges_tax": charges_val,
                    "combined_menu_charges": combined_val,
                    "order_tax": order_val,
                    "recomputed_menu_tax": recomputed_val,
                    "variance_menu_vs_order": variance_menu_order,
                    "variance_combined_vs_order": variance_combined_order,
                    "variance_menu_vs_recomputed": variance_menu_recomputed,
                    "flags": {
                        "menu_order_match": abs(variance_menu_order) <= tolerance,
                        "combined_order_match": abs(variance_combined_order) <= tolerance,
                        "menu_recomputed_match": abs(variance_menu_recomputed) <= tolerance,
                    }
                })

            missing_in_order = [tid for tid in (set(menu_tax_by_id.keys()) | set(charges_tax_by_id.keys())) if tid not in order_tax_by_id]
            order_only = [tid for tid in order_tax_by_id.keys() if tid not in menu_tax_by_id and tid not in charges_tax_by_id]

            summary.update({
                "payment_tax_amount": round(payment_tax_amount, 5),
                "menu_tax_by_id": menu_tax_by_id,
                "order_tax_by_id": order_tax_by_id,
                "recomputed_tax_by_id": recomputed_tax_by_id,
                "charges_tax_by_id": charges_tax_by_id,
                "tax_reconciliation": tax_reconciliation,
                "tax_anomalies": {
                    "missing_in_order": missing_in_order,
                    "order_only": order_only
                }
            })
        except Exception:
            # Fail silently—aggregated view is supplementary
            pass
        # Surface commonly requested top-level metadata (non-sensitive) into summary for CLI convenience
        # Attempt to capture foodAggragetorId if present anywhere typical (root or under paymentDetails/aggregator fields)
        possible_food_agg_keys = [
            "foodAggragetorId",  # as requested (note: keep original spelling if that's how it appears in DB)
            "foodAggregatorId",  # common spelling variant
            "aggregatorId",
            "foodAggregatorID",
        ]
        food_agg_val = None
        for k in possible_food_agg_keys:
            if k in order_data and order_data.get(k) not in (None, ""):
                food_agg_val = order_data.get(k)
                break
        # Fallback: look into paymentDetails or metadata containers if not found at root
        if food_agg_val is None:
            payment_details = order_data.get("paymentDetails") or {}
            for k in possible_food_agg_keys:
                if k in payment_details and payment_details.get(k) not in (None, ""):
                    food_agg_val = payment_details.get(k)
                    break
        if food_agg_val is None:
            meta = order_data.get("metadata") or {}
            if isinstance(meta, dict):
                for k in possible_food_agg_keys:
                    if k in meta and meta.get(k) not in (None, ""):
                        food_agg_val = meta.get(k)
                        break
        if food_agg_val is not None:
            summary["foodAggragetorId"] = str(food_agg_val)

        return {"comparisons": comparisons, "summary": summary}

    # ---------------------------------------------------------------------
    # New integrity check: Validate menuDetails price calculations
    # ---------------------------------------------------------------------
    def _validate_menu_details_calculations(self, order_data: Dict[str, Any], tolerance: float = 1e-5) -> Dict[str, Any]:
        """Validate mathematical correctness of price calculations within menuDetails.price.

        This validation ensures that all price fields within menuDetails are mathematically consistent:
        - grossAmount = unitPrice * qty (before discounts)
        - netAmount = grossAmount - discountAmount (after item discounts)
        - For tax-free items: taxExclusiveUnitPrice = unitPrice, taxAmount = 0.0
        - For taxed items: taxExclusiveUnitPrice = unitPrice / (1 + taxRate)
        - taxExclusiveDiscountAmount = discountAmount / (1 + taxRate) for tax-inclusive
        - taxAmount = (grossAmount - discountAmount) - (taxExclusiveAmount) for tax-inclusive
        - totalPrice:
            Pattern 1 (No discounts): grossAmount
            Patterns 2-4 (any discounts present): grossAmount - discountAmount

        Also validates modifiers/extraDetails pricing with same rules.
        
        NOTE: This validation checks the mathematical relationships within menuDetails.price structure.
        The values may already reflect applied discounts based on the discount pattern (Pattern 1-4).

        Returns dict with:
            is_valid: bool -> True if all calculations are mathematically correct
            total_items: int -> Number of items validated (including modifiers)
            validation_details: List[Dict] -> Per-item validation results
            calculation_errors: List[Dict] -> List of calculation mismatches
        """
        
        menu_items = order_data.get("menuDetails") or []
        # Normalize orderTaxes keys to string for consistent lookup (items/modifiers/charges may hold ObjectId or string)
        order_taxes = {str(tax.get("_id")): tax for tax in order_data.get("orderTaxes", [])}

        # Pre-compute aggregates used for proportional distribution (Pattern 4)
        total_menu_gross = 0.0
        total_item_level_discount_sum = 0.0  # Inclusive, items + modifiers (modifiers multiplied by parent qty)
        total_item_level_tax_excl_discount_sum = 0.0
        total_tax_exclusive_gross_sum = 0.0
        for _mi in menu_items:
            _p = (_mi.get("price") or {})
            qty_tmp = _mi.get("qty", 1) or 1
            gross_tmp = float(_p.get("grossAmount", 0.0))
            total_menu_gross += gross_tmp
            # Add item discount
            total_item_level_discount_sum += float(_p.get("discountAmount", 0.0))
            # Add modifier discounts (each modifier discount multiplied by parent item qty like in _get_total_item_level_discounts)
            for _mod in _mi.get("extraDetails", []) or []:
                _mp = (_mod.get("price") or {})
                mod_disc = float(_mp.get("discountAmount", 0.0) or 0.0)
                if mod_disc:
                    total_item_level_discount_sum += mod_disc * qty_tmp
            total_item_level_tax_excl_discount_sum += float(_p.get("taxExclusiveDiscountAmount", 0.0))
            tax_excl_unit_tmp = float(_p.get("taxExclusiveUnitPrice", 0.0))
            total_tax_exclusive_gross_sum += tax_excl_unit_tmp * qty_tmp
        
        # Check discount pattern context for better error reporting
        has_item_discounts = self._has_item_level_discounts(order_data)
        order_discount = self._get_order_level_discount(order_data)
        
        if has_item_discounts and order_discount > 0:
            # Reclassification: If order discount approximately equals sum of item+modifier discounts (within 5%), treat as Pattern 3
            if total_item_level_discount_sum > 0 and abs(order_discount - total_item_level_discount_sum) / total_item_level_discount_sum < 0.05:
                discount_context = "Pattern 3: Item-Level Discounts Only"
            else:
                discount_context = "Pattern 4: Combined Discounts"
        elif has_item_discounts:
            discount_context = "Pattern 3: Item-Level Discounts Only"
        elif order_discount > 0:
            discount_context = "Pattern 2: Order-Level Discount Only"
        else:
            discount_context = "Pattern 1: No Discounts"
        
        validation_details = []
        calculation_errors = []
        total_items = 0
        
        # ------------------------------------------------------------------
        # Precompute Pattern 4 two-stage allocation (exclusive discounts):
        #   1. Remove item-level exclusive discount per line (items + modifiers)
        #   2. Allocate residual order-level exclusive discount proportionally
        #      to the post-item-discount exclusive bases.
        # This mapping will be used only for netAmount expectations in Pattern 4.
        # ------------------------------------------------------------------
        pattern4_allocation_map = {}
        if 'Pattern 4' in locals().get('discount_context', ''):
            try:
                payment_details = order_data.get("paymentDetails", {}) or {}
                price_details = payment_details.get("priceDetails", {}) or {}
                global_tax_excl_discount_total = float(price_details.get("taxExclusiveDiscountAmount", 0) or 0.0)

                allocation_entries = []  # list of (key, post_item_exclusive_base)
                sum_item_exclusive_discounts = 0.0

                def _get_id(obj):
                    return str(obj.get("internalId") or obj.get("_id") or obj.get("id") or obj.get("name") or "UNK")

                for _item in menu_items:
                    _ip = (_item.get("price") or {})
                    _qty = int(_item.get("qty", 1) or 1)
                    excl_unit = float(_ip.get("taxExclusiveUnitPrice", 0.0) or 0.0)
                    excl_discount = float(_ip.get("taxExclusiveDiscountAmount", 0.0) or 0.0)
                    exclusive_gross = excl_unit * _qty
                    post_item_excl = exclusive_gross - excl_discount
                    item_key = _get_id(_item)
                    allocation_entries.append((item_key, post_item_excl))
                    sum_item_exclusive_discounts += excl_discount

                    # modifiers
                    for _mod in _item.get("extraDetails", []) or []:
                        _mp = (_mod.get("price") or {})
                        mod_excl_unit = float(_mp.get("taxExclusiveUnitPrice", 0.0) or 0.0)
                        mod_qty = int(_mod.get("qty", 1) or 1)
                        mod_excl_discount = float(_mp.get("taxExclusiveDiscountAmount", 0.0) or 0.0)
                        # modifier total exclusive gross multiplied by parent qty
                        mod_exclusive_gross = mod_excl_unit * mod_qty * _qty
                        post_mod_excl = mod_exclusive_gross - (mod_excl_discount * _qty)
                        mod_key = f"{item_key}||mod||{_get_id(_mod)}"
                        allocation_entries.append((mod_key, post_mod_excl))
                        sum_item_exclusive_discounts += (mod_excl_discount * _qty)

                residual_exclusive = max(0.0, global_tax_excl_discount_total - sum_item_exclusive_discounts)
                total_post_item_excl = sum(val for _, val in allocation_entries)
                if total_post_item_excl > 0 and residual_exclusive > 0:
                    for k, base_val in allocation_entries:
                        pattern4_allocation_map[k] = residual_exclusive * (base_val / total_post_item_excl)
                else:
                    # no residual or zero base -> all zero allocations
                    for k, _ in allocation_entries:
                        pattern4_allocation_map[k] = 0.0
            except Exception:
                # Fallback: leave map empty (safe degradation)
                pattern4_allocation_map = {}

        def validate_price_object(item_data, item_type="item", parent_qty=1, parent_internal_id=None):
            """Helper to validate a single price object (item or modifier) with pattern awareness"""
            nonlocal total_items, validation_details, calculation_errors
            
            item_name = item_data.get("name", "Unknown Item")
            item_id = item_data.get("internalId", item_data.get("_id", "Unknown ID"))
            qty = item_data.get("qty", 1)
            price = item_data.get("price", {})
            taxes = item_data.get("taxes", [])
            
            total_items += 1
            
            # Extract price fields (actual values from database)
            unit_price = float(price.get("unitPrice", 0))
            gross_amount = float(price.get("grossAmount", 0))
            tax_exclusive_unit_price = float(price.get("taxExclusiveUnitPrice", 0))
            discount_amount = float(price.get("discountAmount", 0))
            original_item_discount_amount = discount_amount  # preserve original (item-level) discount for pattern checks
            tax_exclusive_discount_amount = float(price.get("taxExclusiveDiscountAmount", 0))
            tax_amount = float(price.get("taxAmount", 0))
            net_amount = float(price.get("netAmount", 0))
            total_price = float(price.get("totalPrice", 0))
            
            # Calculate tax rate from taxes array first
            total_tax_rate = 0.0
            for tax_entry in taxes:
                raw_tax_id = tax_entry.get("taxId") or tax_entry.get("_id")
                tax_id = str(raw_tax_id)
                tax_rate_val = None
                if tax_id in order_taxes:
                    tax_rate_val = order_taxes[tax_id].get("rate")
                # Fallback: sometimes rate may already be embedded in the tax entry
                if tax_rate_val is None:
                    tax_rate_val = tax_entry.get("rate")
                if tax_rate_val is not None:
                    try:
                        total_tax_rate += float(tax_rate_val)
                    except (TypeError, ValueError):
                        pass
            
            # If no tax rate found in taxes array, derive it from price relationships
            if total_tax_rate == 0.0 and tax_exclusive_unit_price > 0 and unit_price > tax_exclusive_unit_price:
                derived_tax_rate = ((unit_price / tax_exclusive_unit_price) - 1) * 100
                total_tax_rate = derived_tax_rate
            
            tax_rate_decimal = total_tax_rate / 100.0
            
            # Pattern-aware expected calculations
            expected_gross_amount = unit_price * qty
            is_taxed = (tax_amount > 0.0 or tax_exclusive_unit_price != unit_price or total_tax_rate > 0.0)
            
            if not is_taxed:
                # Tax-free item logic (pattern-aware for net & totalPrice)
                expected_tax_exclusive_unit_price = unit_price
                expected_tax_exclusive_discount = discount_amount
                expected_tax_amount = 0.0

                # Pattern-aware net amount for non-taxed items mirrors exclusive logic
                if "Pattern 4" in discount_context:
                    # Two-stage residual (exclusive == inclusive for tax-free)
                    payment_details = order_data.get("paymentDetails", {}) or {}
                    price_details = payment_details.get("priceDetails", {}) or {}
                    global_discount_total = float(price_details.get("discountAmount", 0) or 0.0)
                    # residual inclusive discount = global - sum item-level
                    residual_inclusive = max(global_discount_total - total_item_level_discount_sum, 0.0)
                    # Allocate residual over post-item-discount bases
                    post_item_base_total = 0.0
                    if residual_inclusive > 0:
                        for _it in menu_items:
                            _ip = (_it.get("price") or {})
                            _g = float(_ip.get("grossAmount", 0.0) or 0.0)
                            _d = float(_ip.get("discountAmount", 0.0) or 0.0)
                            post_item_base_total += max(_g - _d, 0.0)
                    allocated_residual = 0.0
                    if residual_inclusive > 0:
                        if post_item_base_total > 0:
                            allocated_residual = residual_inclusive * (max(gross_amount - discount_amount, 0.0) / post_item_base_total)
                        else:
                            allocated_residual = residual_inclusive * (gross_amount / total_menu_gross) if total_menu_gross > 0 else 0.0
                    post_item_base = gross_amount - discount_amount
                    expected_net_amount = post_item_base - allocated_residual
                elif "Pattern 3" in discount_context:
                    expected_net_amount = gross_amount - discount_amount
                elif "Pattern 2" in discount_context:
                    payment_details = order_data.get("paymentDetails", {}) or {}
                    price_details = payment_details.get("priceDetails", {}) or {}
                    global_discount_total = float(price_details.get("discountAmount", 0) or 0.0)
                    global_unit_total = float(price_details.get("unitPrice", 0) or 0.0)
                    if global_unit_total > 0 and global_discount_total > 0:
                        proportion = global_discount_total / global_unit_total
                        weighted_disc = proportion * gross_amount
                        expected_net_amount = gross_amount - weighted_disc
                    else:
                        expected_net_amount = gross_amount - discount_amount
                else:  # Pattern 1
                    expected_net_amount = gross_amount - discount_amount

                # totalPrice should always reflect discount for discounted tax-free items
                expected_total_price = gross_amount - discount_amount
                if discount_amount == 0:
                    total_price_formula = f"grossAmount({gross_amount}) (tax-free, no discounts)"
                else:
                    total_price_formula = f"grossAmount({gross_amount}) - discountAmount({discount_amount}) (tax-free)"
            else:
                # Tax-inclusive item logic with pattern awareness
                if tax_rate_decimal > 0:
                    expected_tax_exclusive_unit_price = unit_price / (1 + tax_rate_decimal)
                    expected_tax_exclusive_discount = discount_amount / (1 + tax_rate_decimal)
                else:
                    expected_tax_exclusive_unit_price = tax_exclusive_unit_price
                    expected_tax_exclusive_discount = tax_exclusive_discount_amount
                
                # Calculate expected tax amount base (taxable_amount) with pattern-aware discount composition
                payment_details = order_data.get("paymentDetails", {}) or {}
                price_details = payment_details.get("priceDetails", {}) or {}
                global_unit_price = float(price_details.get("unitPrice", 0))
                global_discount_inclusive = float(price_details.get("discountAmount", 0))  # total (item + order) inclusive discount
                global_tax_excl_discount_total = float(price_details.get("taxExclusiveDiscountAmount", 0))

                if "Pattern 1" in discount_context:
                    taxable_amount = gross_amount
                elif "Pattern 2" in discount_context:
                    # Order-level only: proportional on gross
                    proportion = (global_discount_inclusive / global_unit_price) if (global_unit_price > 0 and global_discount_inclusive > 0) else 0.0
                    weighted_discount_inclusive = proportion * gross_amount
                    taxable_amount = gross_amount - weighted_discount_inclusive
                elif "Pattern 3" in discount_context:
                    # Item-level only
                    taxable_amount = gross_amount - discount_amount
                elif "Pattern 4" in discount_context:
                    # Combined: item-level + allocated order-level share (inclusive)
                    # Unified residual allocation: denominator = global_unit_price - total_item_level_discount_sum.
                    # This represents the post-item-discount tax-inclusive base across all lines (items + modifiers).
                    order_level_inclusive_component = max(global_discount_inclusive - total_item_level_discount_sum, 0.0)
                    allocated_order_inclusive_share = 0.0
                    if order_level_inclusive_component > 0:
                        denom_post_item_discount = max(global_unit_price - total_item_level_discount_sum, 0.0)
                        if denom_post_item_discount > 0:
                            allocated_order_inclusive_share = order_level_inclusive_component * (max(gross_amount - discount_amount, 0.0) / denom_post_item_discount)
                        elif total_menu_gross > 0:
                            # Fallback: gross-based
                            allocated_order_inclusive_share = order_level_inclusive_component * (gross_amount / total_menu_gross)
                    combined_inclusive_discount = discount_amount + allocated_order_inclusive_share
                    taxable_amount = gross_amount - combined_inclusive_discount
                else:
                    taxable_amount = gross_amount - discount_amount
                
                # Calculate expected tax amount using inclusive-tax reverse calculation
                if tax_rate_decimal > 0:
                    expected_tax_amount = taxable_amount - (taxable_amount / (1.0 + tax_rate_decimal))
                else:
                    expected_tax_amount = 0.0
                
                expected_tax_exclusive_gross = expected_tax_exclusive_unit_price * qty

                # Initialize flag before weighted logic (avoid UnboundLocalError for Pattern 1 / tax-free cases)
                weighted_discount_used = False

                # Net Amount expectation logic by pattern:
                # Pattern 1: No discounts -> net = taxExclusiveGross (since no discount)
                # Pattern 2: Order-level only -> retain weighted distribution (global ratio) across items
                # Pattern 3: Item-level only -> net = taxExclusiveGross - taxExclusiveDiscountAmount (no global weighting)
                # Pattern 4: Combined -> apply global weighting (captures effect of both levels)
                global_tex_unit = float(price_details.get("taxExclusiveUnitPrice", 0))
                global_tex_discount = global_tax_excl_discount_total

                # Build a stable key for Pattern 4 allocation map lookup
                if item_type == 'modifier':
                    parent_key_part = str(parent_internal_id or 'PARENT')
                    key_lookup = f"{parent_key_part}||mod||{item_id}"
                else:
                    key_lookup = str(item_id)

                if "Pattern 4" in discount_context:
                    # Two-stage allocation: net = (taxExclGross - itemExclDiscount) - allocatedResidualOrderExcl
                    post_item_excl = expected_tax_exclusive_gross - expected_tax_exclusive_discount
                    allocated_residual = pattern4_allocation_map.get(key_lookup, 0.0)
                    expected_net_amount = post_item_excl - allocated_residual
                    weighted_discount_used = True  # mark to explain formula
                elif "Pattern 3" in discount_context:
                    expected_net_amount = expected_tax_exclusive_gross - expected_tax_exclusive_discount
                elif "Pattern 2" in discount_context:
                    # order-level only -> global proportional exclusive weighting
                    if global_tex_unit > 0 and global_tex_discount > 0:
                        proportion_tex = global_tex_discount / global_tex_unit
                        weighted_item_discount_tex = proportion_tex * expected_tax_exclusive_unit_price * qty
                        expected_net_amount = expected_tax_exclusive_gross - weighted_item_discount_tex
                        weighted_discount_used = True
                    else:
                        expected_net_amount = expected_tax_exclusive_gross - expected_tax_exclusive_discount
                else:  # Pattern 1
                    expected_net_amount = expected_tax_exclusive_gross - expected_tax_exclusive_discount
                # Pattern-aware expected totalPrice
                # Clarified rule: totalPrice always unitPrice*qty - discountAmount (regardless of pattern).
                expected_total_price = gross_amount - discount_amount
                if discount_amount == 0:
                    total_price_formula = f"grossAmount({gross_amount}) (no discounts)"
                else:
                    total_price_formula = f"grossAmount({gross_amount}) - discountAmount({discount_amount})"
            
            # For Pattern 2 and Pattern 4, relax validation for netAmount only (not taxAmount)
            # Previously both taxAmount and netAmount were relaxed which masked real tax variances.
            pattern_adjusted_tolerance = tolerance
            if "Pattern 2" in discount_context or "Pattern 4" in discount_context:
                pattern_adjusted_tolerance = max(tolerance, 1.0)  # Allow up to $1 difference for net amount variance due to distribution
            tax_strict_tolerance = tolerance  # Always enforce strict tolerance for taxAmount
            
            # Validate calculations with pattern-aware tolerance
            item_validation = {
                "item_id": item_id,
                "item_name": item_name,
                "item_type": item_type,
                "qty": qty,
                "tax_rate": total_tax_rate,
                "calculations": {},
                "pattern_context": discount_context
            }
            
            # Check each calculation with appropriate logic and tolerance
            calculations = [
                ("grossAmount", gross_amount, expected_gross_amount, f"unitPrice({unit_price}) × qty({qty})", tolerance),
                ("taxExclusiveUnitPrice", tax_exclusive_unit_price, expected_tax_exclusive_unit_price, 
                 f"unitPrice({unit_price})" if not is_taxed else f"unitPrice({unit_price}) ÷ (1 + {tax_rate_decimal:.5f}) = {expected_tax_exclusive_unit_price:.5f}", tolerance),
                ("taxExclusiveDiscountAmount", tax_exclusive_discount_amount, expected_tax_exclusive_discount,
                 f"discountAmount({discount_amount})" if not is_taxed else f"discountAmount({discount_amount}) ÷ (1 + {tax_rate_decimal:.5f})", tolerance),
                ("taxAmount", tax_amount, expected_tax_amount,
                 "0.0 (tax-free)" if not is_taxed else f"taxable_amount - (taxable_amount ÷ (1 + {tax_rate_decimal:.5f})) = {expected_tax_amount:.5f}", tax_strict_tolerance),
                                ("netAmount", net_amount, expected_net_amount,
                                (f"grossAmount({gross_amount}) - discountAmount({discount_amount})" if not is_taxed else 
                                    (f"Two-stage (P4 net): (taxExclGross - itemExclDisc) - allocatedResidual = {expected_net_amount:.5f}" if ("Pattern 4" in discount_context) else
                                     (f"(weighted) taxExclGross({expected_tax_exclusive_gross:.5f}) - proportion * taxExclGross = {expected_net_amount:.5f}" if weighted_discount_used else
                                      f"taxExclusiveUnitPrice*qty({expected_tax_exclusive_gross:.5f}) - taxExclusiveDiscountAmount({tax_exclusive_discount_amount})"))),
                                 pattern_adjusted_tolerance),
                ("totalPrice", total_price, expected_total_price, total_price_formula, tolerance),
            ]
            
            for field_name, actual, expected, formula, field_tolerance in calculations:
                delta = actual - expected
                is_valid = abs(delta) <= field_tolerance
                
                item_validation["calculations"][field_name] = {
                    "actual": actual,
                    "expected": expected,
                    "delta": round(delta, 8),
                    "is_valid": is_valid,
                    "formula": formula,
                    "tolerance_used": field_tolerance
                }
                
                if not is_valid:
                    calculation_errors.append({
                        "item_id": item_id,
                        "item_name": item_name,
                        "item_type": item_type,
                        "field": field_name,
                        "actual": actual,
                        "expected": expected,
                        "delta": round(delta, 8),
                        "formula": formula,
                        "pattern_context": discount_context,
                        "tolerance_used": field_tolerance
                    })
            
            validation_details.append(item_validation)
        
        # Validate main items and their modifiers hierarchically
        for item in menu_items:
            # Validate main item
            validate_price_object(item, "main_item")
            
            # Store the current main item for hierarchical display
            main_item_validation = validation_details[-1] if validation_details else None
            
            # Validate modifiers/extraDetails if present and group under main item
            extra_details = item.get("extraDetails", [])
            modifier_validations = []
            for modifier in extra_details:
                validate_price_object(modifier, "modifier")
                # Move the modifier validation to be nested under the main item
                if validation_details:
                    modifier_validation = validation_details.pop()  # Remove from main list
                    modifier_validations.append(modifier_validation)
            
            # Add modifiers to the main item validation
            if main_item_validation and modifier_validations:
                main_item_validation["modifiers"] = modifier_validations
        
        is_valid = len(calculation_errors) == 0
        
        return {
            "is_valid": is_valid,
            "total_items": total_items,
            "validation_details": validation_details,
            "calculation_errors": calculation_errors,
            "tolerance": tolerance,
            "discount_context": discount_context,
            "validation_note": self._get_validation_note(discount_context, is_valid, calculation_errors)
        }

    def _get_validation_note(self, discount_context: str, is_valid: bool, calculation_errors: list) -> str:
        """Provide context-aware validation notes based on discount patterns."""
        if is_valid:
            return f"All calculations are mathematically consistent within menuDetails.price structure. ({discount_context})"
        
        if "Pattern 1" in discount_context:
            return ("Pattern 1: No discounts applied. Mathematical inconsistencies indicate "
                   "potential data integrity issues in the source system.")
        
        elif "Pattern 2" in discount_context:
            # Check if errors are related to tax/net amounts which are expected in Pattern 2
            tax_or_net_errors = [err for err in calculation_errors 
                               if err.get('field') in ['taxAmount', 'netAmount']]
            if tax_or_net_errors:
                return ("Pattern 2: Order-level discount detected. Some discrepancies in tax/net amounts "
                       "may be expected as order-level discounts are distributed across items during processing, "
                       "affecting the final calculations stored in menuDetails.price structure.")
            else:
                return ("Pattern 2: Order-level discount detected. Validation errors in non-discount fields "
                       "may indicate data integrity issues.")
        
        elif "Pattern 3" in discount_context:
            return ("Pattern 3: Item-level discounts detected. Calculations should be consistent as "
                   "item discounts are typically applied at the item level before tax calculation.")
        
        elif "Pattern 4" in discount_context:
            return ("Pattern 4: Combined discounts detected. Complex discount interactions may cause "
                   "validation differences between calculated and stored values due to multi-stage "
                   "discount application (item discounts first, then order discount distribution).")
        
        return f"Some price calculations are mathematically inconsistent! ({discount_context})"

    # ---------------------------------------------------------------------
    # Charges validation
    # ---------------------------------------------------------------------
    def _validate_charges(self, order_data: Dict[str, Any], tolerance: float = 1e-5) -> Dict[str, Any]:
        """Validate invoice-included charges integration with order totals and taxes.

        Checks:
        1. Only charges with includeInInvoice == True are included in recomputed total.
           expected_total = unitPrice - discountAmount + sum(invoice_charge.amount)
        2. paymentDetails.priceDetails.totalPrice matches expected_total (within tolerance).
        3. paymentDetails.priceDetails.taxAmount == sum(orderTaxes.amount).
        4. For each charge's taxId, ensure it exists in orderTaxes and charge tax does not exceed orderTax amount.
        5. Charge internal consistency: taxExclusiveAmount + tax == amount and tax == sum(charge.taxes.amount).
        6. Aggregate charge tax per taxId and compare to orderTaxes amounts (exact_match & within_order_tax flags).
        """
        payment_details = order_data.get("paymentDetails", {})
        price_details = payment_details.get("priceDetails", {})
        charges = payment_details.get("charges", []) or []
        order_taxes = order_data.get("orderTaxes", []) or []

        unit_price = float(price_details.get("unitPrice", 0.0) or 0.0)
        discount_amount = float(price_details.get("discountAmount", 0.0) or 0.0)
        stored_total_price = float(price_details.get("totalPrice", 0.0) or 0.0)
        stored_tax_amount = float(price_details.get("taxAmount", 0.0) or 0.0)

        invoice_charges = [c for c in charges if c.get("includeInInvoice")]
        sum_invoice_charge_amounts = 0.0
        charge_entries = []
        charge_tax_by_id: Dict[str, float] = {}
        charge_validation_errors = []

        for ch in invoice_charges:
            amount = float(ch.get("amount", 0.0) or 0.0)
            tax_component = float(ch.get("tax", 0.0) or 0.0)
            tax_exclusive_amount = float(ch.get("taxExclusiveAmount", 0.0) or 0.0)
            taxes_list = ch.get("taxes", []) or []
            sum_invoice_charge_amounts += amount
            sum_list_tax = sum(float(t.get("amount", 0.0) or 0.0) for t in taxes_list)

            internal_ok = True
            if abs(sum_list_tax - tax_component) > tolerance:
                internal_ok = False
                charge_validation_errors.append({
                    "type": ch.get("type"),
                    "issue": "charge_tax_mismatch",
                    "message": f"Charge tax field {tax_component:.5f} != sum(taxes.amount) {sum_list_tax:.5f}"
                })
            if abs((tax_exclusive_amount + tax_component) - amount) > tolerance:
                internal_ok = False
                charge_validation_errors.append({
                    "type": ch.get("type"),
                    "issue": "charge_amount_inconsistent",
                    "message": f"taxExclusiveAmount + tax != amount ({tax_exclusive_amount:.5f} + {tax_component:.5f} != {amount:.5f})"
                })

            # Capture involved taxIds for recomputation
            tax_ids_in_charge = []
            for t in taxes_list:
                raw_tid = t.get("taxId") or t.get("_id")
                tax_amt = float(t.get("amount", 0.0) or 0.0)
                if raw_tid is not None:
                    tax_id = str(raw_tid)
                    tax_ids_in_charge.append(tax_id)
                    charge_tax_by_id.setdefault(tax_id, 0.0)
                    charge_tax_by_id[tax_id] += tax_amt

            # Recompute tax for single-tax charges using inclusive formula: tax = amount - amount/(1+rate)
            recomputed_tax = None
            recomputed_ok = None
            applied_rate = None
            if len(tax_ids_in_charge) == 1:
                tid = tax_ids_in_charge[0]
                # find rate in order_taxes
                for ot in order_taxes:
                    ot_id = str(ot.get("_id") or ot.get("taxId")) if (ot.get("_id") or ot.get("taxId")) else None
                    if ot_id == tid:
                        applied_rate = float(ot.get("rate", ot.get("taxRate", 0.0)) or 0.0)
                        break
                if applied_rate is not None:
                    recomputed_tax = amount - (amount / (1 + applied_rate/100.0))
                    recomputed_tax = round(recomputed_tax, 5)
                    recomputed_ok = abs(recomputed_tax - tax_component) <= tolerance
                    if recomputed_ok is False:
                        internal_ok = False
                        charge_validation_errors.append({
                            "type": ch.get("type"),
                            "issue": "charge_tax_recompute_mismatch",
                            "message": f"Recomputed tax {recomputed_tax:.5f} differs from stored tax {tax_component:.5f} (rate {applied_rate:.2f}%)"
                        })

            charge_entries.append({
                "type": ch.get("type"),
                "amount": amount,
                "tax": tax_component,
                "taxExclusiveAmount": tax_exclusive_amount,
                "sum_list_tax": sum_list_tax,
                "internal_ok": internal_ok,
                "includeInInvoice": True,
                "taxes": taxes_list,
                "recomputed_tax": recomputed_tax,
                "recomputed_tax_match": recomputed_ok,
                "applied_rate": applied_rate,
            })

        expected_total = unit_price - discount_amount + sum_invoice_charge_amounts
        total_ok = abs(expected_total - stored_total_price) <= tolerance

        sum_order_taxes = sum(float(t.get("amount", 0.0) or 0.0) for t in order_taxes)
        tax_total_ok = abs(sum_order_taxes - stored_tax_amount) <= tolerance

        order_tax_map: Dict[str, float] = {}
        for ot in order_taxes:
            raw_id = ot.get("_id") or ot.get("taxId")
            if raw_id is not None:
                tax_id = str(raw_id)
                order_tax_map[tax_id] = float(ot.get("amount", 0.0) or 0.0)

        charge_tax_matches = []
        for tax_id, charge_tax_total in charge_tax_by_id.items():
            # ensure tax_id normalized to string for lookup
            norm_tax_id = str(tax_id)
            order_tax_amount = order_tax_map.get(norm_tax_id)
            if order_tax_amount is None:
                charge_validation_errors.append({
                    "taxId": norm_tax_id,
                    "issue": "charge_tax_not_in_orderTaxes",
                    "message": f"Charge references taxId {norm_tax_id} not present in orderTaxes"
                })
                proportion_ok = False
                exact_match = False
            else:
                proportion_ok = charge_tax_total <= order_tax_amount + tolerance
                exact_match = abs(charge_tax_total - order_tax_amount) <= tolerance
                if not proportion_ok:
                    charge_validation_errors.append({
                        "taxId": norm_tax_id,
                        "issue": "charge_tax_exceeds_order_tax",
                        "message": f"Charge tax {charge_tax_total:.5f} exceeds orderTax amount {order_tax_amount:.5f}"
                    })
            charge_tax_matches.append({
                "taxId": norm_tax_id,
                "charge_tax": round(charge_tax_total,5),
                "order_tax": round(order_tax_amount if order_tax_amount is not None else 0.0,5),
                "exact_match": exact_match,
                "within_order_tax": proportion_ok
            })

        # If high-level reconciliations fail (total or tax) but no granular errors were recorded, add explicit reasons
        if not total_ok and not any(e.get("issue") == "charge_total_mismatch" for e in charge_validation_errors):
            charge_validation_errors.append({
                "issue": "charge_total_mismatch",
                "message": f"Expected total (unitPrice - discount + included charges) {expected_total:.5f} != stored total {stored_total_price:.5f}",
                "expected_total": round(expected_total,5),
                "stored_total": round(stored_total_price,5)
            })
        if not tax_total_ok and not any(e.get("issue") == "charge_tax_total_mismatch" for e in charge_validation_errors):
            charge_validation_errors.append({
                "issue": "charge_tax_total_mismatch",
                "message": f"Sum orderTaxes {sum_order_taxes:.5f} != stored taxAmount {stored_tax_amount:.5f}",
                "sum_order_taxes": round(sum_order_taxes,5),
                "stored_tax_amount": round(stored_tax_amount,5)
            })

        is_valid = total_ok and tax_total_ok and not any(e.get("issue") in [
            "charge_tax_mismatch", "charge_amount_inconsistent", "charge_tax_exceeds_order_tax", "charge_tax_not_in_orderTaxes", "charge_total_mismatch", "charge_tax_total_mismatch"
        ] for e in charge_validation_errors)

        return {
            "is_valid": is_valid,
            "expected_total_price": round(expected_total,5),
            "stored_total_price": round(stored_total_price,5),
            "total_price_match": total_ok,
            "unit_price": unit_price,
            "discount_amount": discount_amount,
            "included_charges_total": round(sum_invoice_charge_amounts,5),
            "included_charge_count": len(invoice_charges),
            "charge_entries": charge_entries,
            "charge_tax_by_id": {k: round(v,5) for k,v in charge_tax_by_id.items()},
            "charge_tax_matches": charge_tax_matches,
            "stored_tax_amount": round(stored_tax_amount,5),
            "sum_order_taxes": round(sum_order_taxes,5),
            "tax_total_match": tax_total_ok,
            "errors": charge_validation_errors,
            "tolerance": tolerance,
        }

    # ---------------------------------------------------------------------
    # New integrity check: Compare menuDetails[] vs itemDetails[] (if present)
    # ---------------------------------------------------------------------
    def _compare_menu_and_item_details(self, order_data: Dict[str, Any], tolerance: float = 1e-5) -> Dict[str, Any]:
        """Validate that duplicated item arrays (menuDetails vs itemDetails) match.

        This comprehensive comparison validates all monetary fields between menuDetails.price
        and itemDetails pricing structures to ensure data consistency across representations.

        Comparison Strategy:
        1. If `itemDetails` absent or empty, mark as not available but not a failure.
        2. Match items by stable identifier (internalId -> _id -> id -> name fallback).
        3. For each matched pair, compare all price fields within tolerance:
           - qty vs quantity
           - price.unitPrice vs unitPrice (or nested price.unitPrice.amount)
           - price.grossAmount vs grossAmount
           - price.taxExclusiveUnitPrice vs taxExclusiveUnitPrice
           - price.discountAmount vs discountAmount
           - price.taxExclusiveDiscountAmount vs taxExclusiveDiscountAmount
           - price.taxAmount vs taxAmount
           - price.netAmount vs netAmount
           - price.totalPrice vs totalPrice
        4. Record any differences exceeding tolerance.

        Returns dict with:
            available: bool        -> Whether itemDetails existed for comparison
            is_consistent: bool    -> True if no material differences
            total_compared: int    -> Number of items compared
            differences: List[Dict]-> Each difference entry
            unmatched_in_menu: int -> Count of menu items without counterpart
            unmatched_in_item: int -> Count of itemDetails without counterpart
            matching_key: str      -> The identifier key used for matching
        """

        menu_items = order_data.get("menuDetails") or []
        raw_item_details = order_data.get("itemDetails") or []

        # Accept either list form or {"items": [...]} container
        if isinstance(raw_item_details, dict):
            item_items = raw_item_details.get("items", []) or []
        else:
            item_items = raw_item_details

        if not item_items:
            return {
                "available": False,
                "reason": "itemDetails array not present or empty",
                "is_consistent": True,
                "total_compared": 0,
                "differences": [],
                "unmatched_in_menu": 0,
                "unmatched_in_item": 0,
                "matching_key": None,
            }

        # Helper to extract a candidate key
        def extract_key(obj: Dict[str, Any]):
            for k in ("internalId", "_id", "id"):
                if k in obj and obj.get(k) not in (None, ""):
                    return str(obj.get(k))
            # Fallback to name (may not be unique)
            return f"NAME::{obj.get('name', '')}"

        # Helper to extract price field from menuDetails.price structure
        def extract_menu_price_field(menu_item: Dict[str, Any], field: str) -> float:
            price_obj = menu_item.get("price") or {}
            value = price_obj.get(field, 0.0)
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        # Helper to extract price field from itemDetails (supports nested price.field.amount)
        def extract_item_price_field(item_detail: Dict[str, Any], field: str) -> float:
            # Check direct field first
            if field in item_detail:
                value = item_detail[field]
                if isinstance(value, dict) and "amount" in value:
                    value = value.get("amount", 0.0)
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return 0.0
            
            # Check nested in price object
            price_obj = item_detail.get("price") or {}
            if field in price_obj:
                value = price_obj[field]
                if isinstance(value, dict) and "amount" in value:
                    value = value.get("amount", 0.0)
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return 0.0
            
            return 0.0

        menu_map = {}
        item_map = {}
        
        # Build maps for matching
        def _get_modifiers(obj: Dict[str, Any]):
            mods = obj.get("extraDetails")
            if not mods:
                mods = obj.get("modifiers")
            return mods or []

        for m in menu_items:
            key = extract_key(m)
            menu_map[key] = m
            # Also index modifiers/extraDetails with a composite key to avoid collision.
            # Key pattern: parentKey||mod||modifierInternalId (fallback to name)
            for mod in _get_modifiers(m):
                mod_key_base = extract_key(m)
                mod_id = None
                for k in ("internalId", "_id", "id"):
                    if k in mod and mod.get(k) not in (None, ""):
                        mod_id = str(mod.get(k))
                        break
                if not mod_id:
                    mod_id = f"NAME::{mod.get('name','')}"
                composite_key = f"{mod_key_base}||mod||{mod_id}"
                # Store enriched modifier object with parent linkage flags
                mod_enriched = dict(mod)
                mod_enriched["__is_modifier__"] = True
                mod_enriched["__parent_key__"] = mod_key_base
                menu_map[composite_key] = mod_enriched
        for it in item_items:
            key = extract_key(it)
            item_map[key] = it
            # Index itemDetails modifiers if present for potential comparison
            for mod in _get_modifiers(it):
                mod_key_base = key
                mod_id = None
                for k in ("internalId", "_id", "id"):
                    if k in mod and mod.get(k) not in (None, ""):
                        mod_id = str(mod.get(k))
                        break
                if not mod_id:
                    mod_id = f"NAME::{mod.get('name','')}"
                composite_key = f"{mod_key_base}||mod||{mod_id}"
                mod_enriched = dict(mod)
                mod_enriched["__is_modifier__"] = True
                mod_enriched["__parent_key__"] = mod_key_base
                item_map[composite_key] = mod_enriched

        differences: List[Dict[str, Any]] = []
        unmatched_in_menu = 0
        unmatched_in_item = 0
        total_compared = 0

        # Define all fields to compare between menuDetails.price and itemDetails
        fields_to_check = [
            ("qty", lambda m: int(m.get("qty", 1)), lambda i: int(i.get("qty", i.get("quantity", 1)))),
            ("unitPrice", lambda m: extract_menu_price_field(m, "unitPrice"), lambda i: extract_item_price_field(i, "unitPrice")),
            ("grossAmount", lambda m: extract_menu_price_field(m, "grossAmount"), lambda i: extract_item_price_field(i, "grossAmount")),
            ("taxExclusiveUnitPrice", lambda m: extract_menu_price_field(m, "taxExclusiveUnitPrice"), lambda i: extract_item_price_field(i, "taxExclusiveUnitPrice")),
            ("discountAmount", lambda m: extract_menu_price_field(m, "discountAmount"), lambda i: extract_item_price_field(i, "discountAmount")),
            ("taxExclusiveDiscountAmount", lambda m: extract_menu_price_field(m, "taxExclusiveDiscountAmount"), lambda i: extract_item_price_field(i, "taxExclusiveDiscountAmount")),
            ("taxAmount", lambda m: extract_menu_price_field(m, "taxAmount"), lambda i: extract_item_price_field(i, "taxAmount")),
            ("netAmount", lambda m: extract_menu_price_field(m, "netAmount"), lambda i: extract_item_price_field(i, "netAmount")),
            ("totalPrice", lambda m: extract_menu_price_field(m, "totalPrice"), lambda i: extract_item_price_field(i, "totalPrice")),
        ]

        # Keyed comparison
        all_keys = set(menu_map.keys()) | set(item_map.keys())
        items_detail = []  # Detailed information for each item comparison
        
        for key in all_keys:
            m_entry = menu_map.get(key)
            i_entry = item_map.get(key)
            
            if m_entry is None:
                # ItemDetails has an entry not in menu; count only if not a modifier
                if not (i_entry and i_entry.get("__is_modifier__")):
                    unmatched_in_item += 1
                continue
            if i_entry is None:
                # Menu has an entry not in itemDetails; ignore modifiers for mismatch tally
                if not (m_entry and m_entry.get("__is_modifier__")):
                    unmatched_in_menu += 1
                # Still record standalone modifier details for visibility, but skip comparison
                if m_entry.get("__is_modifier__"):
                    items_detail.append({
                        "key": key,
                        "name": m_entry.get("name","Unknown Modifier"),
                        "fields": {},
                        "is_modifier": True,
                        "parent_key": m_entry.get("__parent_key__"),
                        "note": "Modifier exists only in menuDetails"
                    })
                continue
                
            total_compared += 1
            
            # Collect detailed field information for this item
            item_detail = {
                "key": key,
                "name": m_entry.get("name", "Unknown Item"),
                "fields": {},
                "is_modifier": bool(m_entry.get("__is_modifier__")),
                "parent_key": m_entry.get("__parent_key__") if m_entry.get("__is_modifier__") else None
            }
            
            for field_name, menu_extractor, item_extractor in fields_to_check:
                try:
                    mv = menu_extractor(m_entry)
                    iv = item_extractor(i_entry)
                    delta = mv - iv
                    
                    # Store detailed field information
                    item_detail["fields"][field_name] = {
                        "menu_value": mv,
                        "item_value": iv,
                        "delta": round(delta, 5)
                    }
                    
                    # Also collect differences for backward compatibility
                    if abs(delta) > tolerance:
                        differences.append({
                            "key": key,
                            "field": field_name,
                            "menu_value": mv,
                            "item_value": iv,
                            "delta": round(delta, 5)
                        })
                except Exception:  # defensive: ignore extraction errors per field
                    item_detail["fields"][field_name] = {
                        "menu_value": "ERR",
                        "item_value": "ERR",
                        "delta": None
                    }
                    differences.append({
                        "key": key,
                        "field": field_name,
                        "menu_value": "ERR",
                        "item_value": "ERR",
                        "delta": None,
                        "issue": "extraction_error"
                    })
            
            items_detail.append(item_detail)

        # Consistency ignores modifier-only presence mismatches
        is_consistent = (
            len(differences) == 0 and unmatched_in_menu == 0 and unmatched_in_item == 0
        )

        return {
            "available": True,
            "is_consistent": is_consistent,
            "total_compared": total_compared,
            "differences": differences,
            "unmatched_in_menu": unmatched_in_menu,
            "unmatched_in_item": unmatched_in_item,
            "matching_key": "identifier",
            "tolerance": tolerance,
            "items_detail": items_detail,  # Includes modifiers with parent linkage
        }
