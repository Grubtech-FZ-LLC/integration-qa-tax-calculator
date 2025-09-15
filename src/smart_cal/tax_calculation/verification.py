"""Order tax verification service (simplified from legacy implementation)."""

from __future__ import annotations

from typing import Any, Dict, List

from .repository import OrderRepository


class TaxVerificationService:
    """High-level service for tax verification operations."""
    
    def __init__(self, db_name: str = None):
        self.verifier = OrderTaxVerifier()
        self.db_name = db_name
        
    def verify_order_by_id(self, order_id: str) -> Dict[str, Any]:
        """Verify tax calculations for an order by its internal ID."""
        with OrderRepository(db_name=self.db_name) as repo:
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

    def _determine_pattern(self, order_data: Dict[str, Any]) -> str:
        """Determine which discount pattern applies to this order."""
        has_item_discounts = self._has_item_level_discounts(order_data)
        order_discount = self._get_order_level_discount(order_data)
        
        if has_item_discounts and order_discount > 0:
            return "Pattern 4: Combined (Item + Order Level Discounts)"
        elif has_item_discounts:
            return "Pattern 3: Item-Level Discounts Only"
        elif order_discount > 0:
            return "Pattern 2: Order-Level Discount Only"
        else:
            return "Pattern 1: No Discounts"

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

        summary = {
            "total_taxes": len(comparisons),
            "mismatches": sum(1 for c in comparisons if not c["is_matching"]),
            "total_difference": round(sum(c["menu_order_diff"] for c in comparisons), 5),
            "pattern_info": {
                "has_item_discounts": self._has_item_level_discounts(order_data),
                "order_discount": self._get_order_level_discount(order_data),
                "item_discounts": self._get_total_item_level_discounts(order_data),
                "remaining_order_discount": max(0.0, self._get_order_level_discount(order_data) - self._get_total_item_level_discounts(order_data)) if self._has_item_level_discounts(order_data) and self._get_order_level_discount(order_data) > 0 else (0.0 if self._has_item_level_discounts(order_data) else self._get_order_level_discount(order_data)),
                "pattern": self._determine_pattern(order_data)
            }
        }
        return {"comparisons": comparisons, "summary": summary}
