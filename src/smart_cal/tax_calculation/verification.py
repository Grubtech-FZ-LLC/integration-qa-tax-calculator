"""Order tax verification service (simplified from legacy implementation)."""

from __future__ import annotations

from typing import Any, Dict, List

from .repository import OrderRepository


class TaxVerificationService:
    """High-level service for tax verification operations."""
    
    def __init__(self, db_name: str = None, connection_url_env_key: str = None, precision: int = 5):
        self.verifier = OrderTaxVerifier(precision=precision)
        self.db_name = db_name
        self.connection_url_env_key = connection_url_env_key
        self.precision = precision
        
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
                'summary': result.get('summary', {}),
                'orderTaxes': result.get('orderTaxes', [])  # Pass through orderTaxes for CLI validation
            }


class OrderTaxVerifier:
    """Compare calculated menu taxes with stored order taxes.

    Enhanced verifier with precision support that:
    - Iterates menuDetails (items and modifiers) with 5-decimal precision
    - Detects discount patterns (Patterns 1-4) with improved tolerance handling
    - Recomputes tax using r_j/(1+R) formula with decoupled calculation logic
    - Provides transparent per-tax component breakdown and validation
    - Returns per-taxId comparison with detailed analysis
    """

    def __init__(self, precision: int = 5) -> None:
        self.precision = precision
        self._precision_multiplier = 10 ** precision
    
    def _r(self, taxable_amount: float, rate_j: float, total_rate_R: float) -> float:
        """
        Calculate per-tax component using r_j/(1+R) formula with precision control.
        
        This method implements the core tax calculation formula:
        tax_component_j = taxable_amount * (r_j / (1 + R))
        
        Where:
        - r_j is the individual tax rate (as decimal, e.g., 0.05 for 5%)
        - R is the sum of all applicable tax rates (as decimal)
        - taxable_amount is the tax-inclusive amount after discount allocation
        
        Args:
            taxable_amount: Tax-inclusive amount to calculate tax from
            rate_j: Individual tax rate as decimal (e.g., 0.05 for 5%)
            total_rate_R: Sum of all applicable tax rates as decimal
            
        Returns:
            Calculated tax component rounded to specified precision
        """
        if taxable_amount <= 0 or rate_j <= 0 or total_rate_R <= 0:
            return 0.0
            
        # Apply r_j/(1+R) formula for proportional tax allocation
        tax_component = taxable_amount * (rate_j / (1.0 + total_rate_R))
        
        # Round to specified precision
        return round(tax_component, self.precision)

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
        normalized_tax_id = self._normalize_id(tax_id)
        for order_tax in order_data.get("orderTaxes", []):
            order_tax_id = self._normalize_id(order_tax.get("_id", order_tax.get("taxId", "")))
            if order_tax_id == normalized_tax_id:
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
        """
        Enhanced tax recomputation with decoupled calculation logic and 5-decimal precision.
        
        This method implements pattern-aware tax calculation using the r_j/(1+R) formula:
        1. Determines discount pattern with enhanced validation
        2. Calculates taxable bases for items and modifiers based on pattern
        3. Applies r_j/(1+R) formula for precise tax component calculation
        4. Returns aggregated tax amount rounded to specified precision
        
        Args:
            order_data: Complete order data dictionary
            tax_id: Target tax ID to calculate
            
        Returns:
            Recomputed tax amount with precision control
        """
        # Store order data for decoupled rate lookups
        self._current_order_data = order_data
        
        # Get tax rate and validate
        rate_percent = self._get_tax_rate_by_id(order_data, tax_id)
        if rate_percent <= 0:
            return 0.0
        
        rate_decimal = rate_percent / 100.0
        
        # Get enhanced pattern validation with precision-aware tolerance
        pattern_info = self._validate_discount_consistency(order_data)
        pattern_code = pattern_info["pattern_code"]
        
        # Get discount allocation parameters for decoupled calculation
        allocation_params = self._get_discount_allocation_params(order_data, pattern_code)
        
        # DECOUPLED TAX CALCULATION: Calculate tax amounts independently
        total_tax = 0.0
        
        # Process items
        for item in order_data.get("menuDetails", []):
            # Check if item has this tax_id
            item_has_tax = any(str(t.get("taxId")) == str(tax_id) for t in item.get("taxes", []))
            if item_has_tax:
                item_tax = self._calculate_decoupled_tax_amount(
                    item, tax_id, allocation_params, pattern_code
                )
                total_tax += item_tax
            
            # Process modifiers for this item
            qty = int(item.get("qty", 1))
            for mod in item.get("extraDetails", []):
                mod_has_tax = any(str(t.get("taxId")) == str(tax_id) for t in mod.get("taxes", []))
                if mod_has_tax:
                    mod_tax = self._calculate_decoupled_tax_amount(
                        mod, tax_id, allocation_params, pattern_code, parent_qty=qty
                    )
                    total_tax += mod_tax
        
        return round(total_tax, self.precision)
    
    def _calculate_decoupled_tax_amount(self, item_data: Dict[str, Any], tax_id: str, 
                                      allocation_params: Dict[str, Any], pattern_code: int,
                                      parent_qty: int = 1) -> float:
        """
        Calculate tax amount completely decoupled from database grossAmount/netAmount.
        
        This method:
        1. Calculates taxable amount from unitPrice × qty and discount patterns
        2. Gets tax rates from orderTaxes (not from stored amounts)
        3. Applies r_j/(1+R) formula for precise tax calculation
        4. Ignores any potentially incorrect grossAmount/netAmount in database
        
        Returns the mathematically correct tax amount based on business logic.
        """
        # Get core data - never trust derived fields in database
        price_info = item_data.get("price", {})
        unit_price = float(price_info.get("unitPrice", 0.0))
        qty = int(item_data.get("qty", 1))
        
        # Skip if no price
        if unit_price <= 0:
            return 0.0
        
        # Get tax rate for this specific tax ID
        individual_rate = self._get_item_tax_rate(item_data, tax_id)
        if individual_rate <= 0:
            return 0.0
        
        # Get total tax rate for this item (sum of all applicable taxes)
        total_rate = self._get_total_tax_rate(item_data, "percent")
        if total_rate <= 0:
            return 0.0
        
        # Calculate taxable amount using decoupled method
        taxable_amount = self._calculate_item_taxable_amount(
            item_data, allocation_params, pattern_code, parent_qty
        )
        
        if taxable_amount <= 0:
            return 0.0
        
        # Apply r_j/(1+R) formula with proper rates
        individual_rate_decimal = individual_rate / 100.0
        total_rate_decimal = total_rate / 100.0
        
        tax_amount = self._r(taxable_amount, individual_rate_decimal, total_rate_decimal)
        
        # For modifiers, multiply by parent quantity
        if parent_qty > 1:
            tax_amount *= parent_qty
        
        return tax_amount
    
    def _calculate_taxable_bases(self, order_data: Dict[str, Any], tax_id: str, pattern_code: int) -> List[Dict[str, Any]]:
        """
        Calculate taxable bases for all menu items and modifiers based on discount pattern.
        
        This method provides decoupled taxable amount calculation supporting all patterns:
        - Pattern 1: No discounts (taxable = gross amount)  
        - Pattern 2: Order-level discount (proportional allocation)
        - Pattern 3: Item-level discounts (post-discount amounts)
        - Pattern 4: Combined discounts (two-stage allocation)
        
        Args:
            order_data: Complete order data dictionary
            tax_id: Target tax ID for filtering relevant items/modifiers
            pattern_code: Numeric pattern code (1-4) from enhanced validation
            
        Returns:
            List of taxable base dictionaries with amount and rate information
        """
        taxable_bases = []
        
        # Get pattern-specific discount allocation parameters
        allocation_params = self._get_discount_allocation_params(order_data, pattern_code)
        
        # Process each menu item
        for item in order_data.get("menuDetails", []):
            # Check if item has this tax_id
            item_tax_rate = self._get_item_tax_rate(item, tax_id)
            if item_tax_rate <= 0:
                continue
                
            # Calculate item taxable amount based on pattern
            item_taxable = self._calculate_item_taxable_amount(
                item, allocation_params, pattern_code
            )
            
            if item_taxable > 0:
                taxable_bases.append({
                    "type": "item",
                    "item_id": item.get("internalId", "unknown"),
                    "item_name": item.get("name", "Unknown Item"),
                    "taxable_amount": item_taxable,
                    "individual_rate_decimal": item_tax_rate / 100.0,
                    "total_rate_decimal": self._get_total_tax_rate(item, "decimal"),
                    "qty": int(item.get("qty", 1))
                })
            
            # Process modifiers for this item
            qty = int(item.get("qty", 1))
            for mod in item.get("extraDetails", []):
                mod_tax_rate = self._get_item_tax_rate(mod, tax_id) 
                if mod_tax_rate <= 0:
                    continue
                    
                # Calculate modifier taxable amount (multiplied by parent qty later)
                mod_taxable_base = self._calculate_item_taxable_amount(
                    mod, allocation_params, pattern_code, parent_qty=qty
                )
                
                if mod_taxable_base > 0:
                    taxable_bases.append({
                        "type": "modifier",
                        "item_id": mod.get("internalId", "unknown"),
                        "item_name": mod.get("name", "Unknown Modifier"),
                        "parent_item": item.get("name", "Unknown Item"),
                        "taxable_amount": mod_taxable_base * qty,  # Apply parent qty
                        "individual_rate_decimal": mod_tax_rate / 100.0,
                        "total_rate_decimal": self._get_total_tax_rate(mod, "decimal"),
                        "parent_qty": qty
                    })
        
        return taxable_bases
    
    def _get_discount_allocation_params(self, order_data: Dict[str, Any], pattern_code: int) -> Dict[str, Any]:
        """Get discount allocation parameters based on pattern for consistent calculation."""
        payment_details = order_data.get("paymentDetails", {})
        price_details = payment_details.get("priceDetails", {})
        
        params = {
            "pattern_code": pattern_code,
            "order_discount": self._get_order_level_discount(order_data),
            "item_discounts": self._get_total_item_level_discounts(order_data),
            "unit_price": float(price_details.get("unitPrice", 0.0)),
            "calculated_subtotal": self._get_calculated_subtotal(order_data)
        }
        
        # Calculate remaining order discount for Pattern 4
        if pattern_code == 4:
            params["remaining_order_discount"] = max(0.0, 
                params["order_discount"] - params["item_discounts"]
            )
            params["post_item_subtotal"] = params["unit_price"] - params["item_discounts"]
        else:
            params["remaining_order_discount"] = 0.0
            params["post_item_subtotal"] = 0.0
        
        return params
    
    def _calculate_item_taxable_amount(self, item_data: Dict[str, Any], 
                                     allocation_params: Dict[str, Any], 
                                     pattern_code: int, 
                                     parent_qty: int = 1) -> float:
        """
        Calculate taxable amount DECOUPLED from database grossAmount/netAmount fields.
        
        This method calculates taxable amounts purely from core data:
        - unitPrice × qty (base amount)
        - discountAmount (item-level discount)  
        - Discount pattern allocation logic
        
        Does NOT depend on potentially incorrect grossAmount/netAmount in database.
        """
        price_info = item_data.get("price", {})
        unit_price = float(price_info.get("unitPrice", 0.0))
        qty = int(item_data.get("qty", 1))
        item_discount = float(price_info.get("discountAmount", 0.0))
        
        # Calculate base amount from core data (not from database grossAmount)
        base_amount = unit_price * qty
        
        if pattern_code == 1:
            # Pattern 1: No discounts - pure base amount
            return base_amount
            
        elif pattern_code == 2:
            # Pattern 2: Order-level discount proportional allocation
            order_discount = allocation_params["order_discount"]
            unit_price_total = allocation_params["unit_price"]  # Global unit price
            
            if order_discount > 0 and unit_price_total > 0:
                # Allocate order discount proportionally
                proportion = order_discount / unit_price_total
                allocated_discount = proportion * base_amount
                return base_amount - allocated_discount
            else:
                return base_amount
                
        elif pattern_code == 3:
            # Pattern 3: Item-level discounts only
            return base_amount - item_discount
            
        elif pattern_code == 4:
            # Pattern 4: Combined discounts (two-stage)
            # Step 1: Apply item-level discount
            post_item_amount = base_amount - item_discount
            
            # Step 2: Apply residual order discount
            remaining_order_discount = allocation_params["remaining_order_discount"]
            post_item_subtotal = allocation_params["post_item_subtotal"]
            
            if remaining_order_discount > 0 and post_item_subtotal > 0:
                proportion = remaining_order_discount / post_item_subtotal
                allocated_residual = proportion * post_item_amount
                return post_item_amount - allocated_residual
            else:
                return post_item_amount
        
        # Fallback: base minus item discount
        return base_amount - item_discount
    
    def _normalize_id(self, obj_id) -> str:
        """Normalize ObjectId or string ID to consistent string format."""
        if obj_id is None:
            return ""
        # Convert ObjectId to string (gets hex representation)
        return str(obj_id)
    
    def _get_item_tax_rate(self, item_data: Dict[str, Any], tax_id: str) -> float:
        """Get tax rate for specific tax ID from item's taxes array or orderTaxes."""
        # Normalize the target tax_id
        normalized_tax_id = self._normalize_id(tax_id)
        
        # First check if item has this tax_id (without caring about rate)
        has_tax_id = False
        for tax_entry in item_data.get("taxes", []):
            if self._normalize_id(tax_entry.get("taxId", "")) == normalized_tax_id:
                has_tax_id = True
                # Try to get rate from tax entry first
                rate = tax_entry.get("rate")
                if rate is not None:
                    return float(rate)
                break
        
        # If item doesn't have this tax_id, return 0
        if not has_tax_id:
            return 0.0
        
        # If item has tax_id but no rate, get rate from orderTaxes (decoupled approach)
        if hasattr(self, '_current_order_data') and self._current_order_data:
            for order_tax in self._current_order_data.get("orderTaxes", []):
                order_tax_id = self._normalize_id(order_tax.get("_id", order_tax.get("taxId", "")))
                if order_tax_id == normalized_tax_id:
                    return float(order_tax.get("rate", order_tax.get("taxRate", 0.0)))
        
        return 0.0
    
    def _get_total_tax_rate(self, item_data: Dict[str, Any], format_type: str = "decimal") -> float:
        """Calculate total tax rate for item (sum of all applicable tax rates)."""
        total_rate = 0.0
        
        for tax_entry in item_data.get("taxes", []):
            tax_id = self._normalize_id(tax_entry.get("taxId", ""))
            rate = tax_entry.get("rate")
            
            if rate is not None:
                total_rate += float(rate)
            elif hasattr(self, '_current_order_data') and self._current_order_data:
                # Get rate from orderTaxes (decoupled approach)
                for order_tax in self._current_order_data.get("orderTaxes", []):
                    order_tax_id = self._normalize_id(order_tax.get("_id", order_tax.get("taxId", "")))
                    if order_tax_id == tax_id:
                        total_rate += float(order_tax.get("rate", order_tax.get("taxRate", 0.0)))
                        break
        

        
        if format_type == "decimal":
            return total_rate / 100.0
        else:
            return total_rate
    
    def _per_tax_component_breakdown(self, order_data: Dict[str, Any], tax_id: str) -> Dict[str, Any]:
        """
        Generate transparent per-tax component breakdown using r_j/(1+R) formula.
        
        This method provides detailed analysis of tax calculations for validation:
        1. Lists all items/modifiers that contribute to this tax ID
        2. Shows taxable amount derivation for each component based on discount pattern
        3. Applies r_j/(1+R) formula with precision control for each component
        4. Aggregates results with detailed breakdown for transparency
        5. Compares with stored menuDetails tax amounts for validation
        
        Args:
            order_data: Complete order data dictionary  
            tax_id: Target tax ID for detailed breakdown
            
        Returns:
            Dictionary containing detailed component analysis and validation
        """
        # Store order data for rate lookup (needed by _get_total_tax_rate)
        self._current_order_data = order_data
        
        # Get tax rate and validate
        rate_percent = self._get_tax_rate_by_id(order_data, tax_id)
        if rate_percent <= 0:
            return {
                "tax_id": tax_id,
                "rate_percent": 0.0,
                "components": [],
                "totals": {"expected": 0.0, "recomputed": 0.0, "variance": 0.0},
                "validation": {"is_valid": True, "message": "No applicable tax rate found"}
            }
        
        rate_decimal = rate_percent / 100.0
        
        # Get enhanced pattern information
        pattern_info = self._validate_discount_consistency(order_data)
        pattern_code = pattern_info["pattern_code"]
        
        # Get discount allocation parameters  
        allocation_params = self._get_discount_allocation_params(order_data, pattern_code)
        
        components = []
        total_expected = 0.0
        total_recomputed = 0.0
        
        # Process each menu item
        for item in order_data.get("menuDetails", []):
            # Check if item has this tax_id and get expected amount
            expected_item_tax = 0.0
            for tax_entry in item.get("taxes", []):
                if str(tax_entry.get("taxId", "")) == str(tax_id):
                    expected_item_tax += float(tax_entry.get("amount", 0.0))
            
            if expected_item_tax > 0:
                # Calculate recomputed tax for item
                item_taxable = self._calculate_item_taxable_amount(item, allocation_params, pattern_code)
                total_rate_R = self._get_total_tax_rate(item, "decimal")
                
                if item_taxable > 0 and total_rate_R > 0:
                    recomputed_item_tax = self._r(item_taxable, rate_decimal, total_rate_R)
                else:
                    recomputed_item_tax = 0.0
                
                # Build component detail
                price_info = item.get("price", {})
                component = {
                    "type": "item",
                    "item_id": item.get("internalId", "unknown"),
                    "item_name": item.get("name", "Unknown Item"),
                    "qty": int(item.get("qty", 1)),
                    "unit_price": round(float(price_info.get("unitPrice", 0.0)), self.precision),
                    "total_price": round(float(price_info.get("totalPrice", 0.0)), self.precision),
                    "item_discount": round(float(price_info.get("discountAmount", 0.0)), self.precision),
                    "taxable_amount": round(item_taxable, self.precision),
                    "individual_rate_percent": rate_percent,
                    "total_rate_percent": self._get_total_tax_rate(item, "percent"),
                    "formula_components": {
                        "r_j": rate_decimal,
                        "R_total": total_rate_R,
                        "r_j_over_1_plus_R": round(rate_decimal / (1.0 + total_rate_R), self.precision) if total_rate_R > 0 else 0.0
                    },
                    "expected_tax": expected_item_tax,  # Exact DB value
                    "recomputed_tax": round(recomputed_item_tax, self.precision),
                    "variance": round(expected_item_tax - recomputed_item_tax, self.precision),
                    "pattern_details": self._get_component_pattern_details(item, allocation_params, pattern_code)
                }
                
                components.append(component)
                total_expected += expected_item_tax
                total_recomputed += recomputed_item_tax
            
            # Process modifiers
            qty = int(item.get("qty", 1))
            for mod in item.get("extraDetails", []):
                expected_mod_tax = 0.0
                for tax_entry in mod.get("taxes", []):
                    if str(tax_entry.get("taxId", "")) == str(tax_id):
                        expected_mod_tax += float(tax_entry.get("amount", 0.0))
                
                if expected_mod_tax > 0:
                    # Calculate recomputed tax for modifier (base calculation)
                    mod_taxable_base = self._calculate_item_taxable_amount(mod, allocation_params, pattern_code)
                    total_rate_R_mod = self._get_total_tax_rate(mod, "decimal")
                    
                    if mod_taxable_base > 0 and total_rate_R_mod > 0:
                        recomputed_mod_base = self._r(mod_taxable_base, rate_decimal, total_rate_R_mod)
                        recomputed_mod_final = recomputed_mod_base * qty  # Apply parent quantity
                    else:
                        recomputed_mod_base = 0.0
                        recomputed_mod_final = 0.0
                    
                    # Build modifier component detail
                    mod_price_info = mod.get("price", {})
                    mod_component = {
                        "type": "modifier",
                        "item_id": mod.get("internalId", "unknown"),
                        "item_name": mod.get("name", "Unknown Modifier"),
                        "parent_item": item.get("name", "Unknown Item"),
                        "parent_qty": qty,
                        "unit_price": round(float(mod_price_info.get("unitPrice", 0.0)), self.precision),
                        "total_price": round(float(mod_price_info.get("totalPrice", 0.0)), self.precision),
                        "modifier_discount": round(float(mod_price_info.get("discountAmount", 0.0)), self.precision),
                        "taxable_amount_base": round(mod_taxable_base, self.precision),
                        "taxable_amount_final": round(mod_taxable_base * qty, self.precision),
                        "individual_rate_percent": rate_percent,
                        "total_rate_percent": self._get_total_tax_rate(mod, "percent"),
                        "formula_components": {
                            "r_j": rate_decimal,
                            "R_total": total_rate_R_mod,
                            "r_j_over_1_plus_R": round(rate_decimal / (1.0 + total_rate_R_mod), self.precision) if total_rate_R_mod > 0 else 0.0
                        },
                        "expected_tax": expected_mod_tax,  # Exact DB value
                        "recomputed_tax_base": round(recomputed_mod_base, self.precision),
                        "recomputed_tax_final": round(recomputed_mod_final, self.precision),
                        "variance": round(expected_mod_tax - recomputed_mod_base, self.precision),  # Compare to base
                        "pattern_details": self._get_component_pattern_details(mod, allocation_params, pattern_code, parent_qty=qty)
                    }
                    
                    components.append(mod_component)
                    total_expected += expected_mod_tax
                    total_recomputed += recomputed_mod_final
        
        # Aggregate totals and validation
        total_variance = round(total_expected - total_recomputed, self.precision)
        tolerance = 1.0 / self._precision_multiplier
        is_valid = abs(total_variance) <= tolerance
        
        return {
            "tax_id": tax_id,
            "rate_percent": rate_percent,
            "pattern_info": {
                "pattern": pattern_info["pattern"],
                "pattern_code": pattern_code,
                "corrected": pattern_info.get("corrected_pattern", False)
            },
            "components": components,
            "totals": {
                "expected": total_expected,  # Exact sum of DB values
                "recomputed": round(total_recomputed, self.precision),
                "variance": total_variance
            },
            "validation": {
                "is_valid": is_valid,
                "tolerance": tolerance,
                "message": "Tax calculation validation passed" if is_valid else f"Variance {total_variance} exceeds tolerance {tolerance}"
            },
            "formula_explanation": f"r_j/(1+R) where r_j={rate_percent}% (this tax) and R=sum of all applicable tax rates per item"
        }
    
    def _get_component_pattern_details(self, item_data: Dict[str, Any], 
                                     allocation_params: Dict[str, Any], 
                                     pattern_code: int,
                                     parent_qty: int = 1) -> Dict[str, Any]:
        """Generate detailed pattern-specific calculation breakdown for transparency."""
        price_info = item_data.get("price", {})
        total_price = float(price_info.get("totalPrice", 0.0))
        item_discount = float(price_info.get("discountAmount", 0.0))
        
        details = {
            "pattern_code": pattern_code,
            "gross_amount": round(total_price + item_discount, self.precision) if item_discount > 0 else round(total_price, self.precision),
            "item_level_discount": round(item_discount, self.precision),
            "post_item_amount": round(total_price, self.precision)
        }
        
        if pattern_code == 1:
            details.update({
                "pattern_name": "No Discounts", 
                "calculation": "taxable_amount = gross_amount",
                "distributed_order_discount": 0.0,
                "final_taxable": round(total_price, self.precision)
            })
            
        elif pattern_code == 2:
            order_discount = allocation_params["order_discount"]
            calculated_subtotal = allocation_params["calculated_subtotal"]
            
            if order_discount > 0 and calculated_subtotal > 0:
                distributed_discount = (order_discount / calculated_subtotal) * total_price
            else:
                distributed_discount = 0.0
                
            details.update({
                "pattern_name": "Order-Level Discount Only",
                "calculation": f"taxable_amount = total_price - (order_discount / subtotal) * total_price",
                "order_discount_total": round(order_discount, self.precision),
                "calculated_subtotal": round(calculated_subtotal, self.precision),
                "proportion": round(order_discount / calculated_subtotal, self.precision) if calculated_subtotal > 0 else 0.0,
                "distributed_order_discount": round(distributed_discount, self.precision),
                "final_taxable": round(total_price - distributed_discount, self.precision)
            })
            
        elif pattern_code == 3:
            details.update({
                "pattern_name": "Item-Level Discounts Only",
                "calculation": "taxable_amount = total_price (already includes item discount)",
                "distributed_order_discount": 0.0,
                "final_taxable": round(total_price, self.precision)
            })
            
        elif pattern_code == 4:
            remaining_order_discount = allocation_params["remaining_order_discount"]
            post_item_subtotal = allocation_params["post_item_subtotal"]
            
            if remaining_order_discount > 0 and post_item_subtotal > 0:
                distributed_order_discount = (remaining_order_discount / post_item_subtotal) * total_price
            else:
                distributed_order_discount = 0.0
                
            details.update({
                "pattern_name": "Combined Discounts",
                "calculation": "taxable_amount = post_item_amount - (remaining_order_discount / post_item_subtotal) * post_item_amount",
                "total_order_discount": round(allocation_params["order_discount"], self.precision),
                "item_discounts_total": round(allocation_params["item_discounts"], self.precision),
                "remaining_order_discount": round(remaining_order_discount, self.precision),
                "post_item_subtotal": round(post_item_subtotal, self.precision),
                "proportion": round(remaining_order_discount / post_item_subtotal, self.precision) if post_item_subtotal > 0 else 0.0,
                "distributed_order_discount": round(distributed_order_discount, self.precision),
                "final_taxable": round(total_price - distributed_order_discount, self.precision)
            })
        
        # Apply parent quantity for modifiers
        if parent_qty > 1:
            details["parent_qty"] = parent_qty
            details["final_taxable_with_qty"] = round(details["final_taxable"] * parent_qty, self.precision)
        
        return details

    def _build_details(self, order_data: Dict[str, Any], tax_id: str) -> Dict[str, Any]:
        # Set current order data for tax rate lookups
        self._current_order_data = order_data
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
                        # Use gross amount (unitPrice × qty) as the basis for discount distribution
                        gross_amount = float(item.get("price", {}).get("unitPrice", 0.0)) * qty
                        calculated_subtotal = self._get_calculated_subtotal(order_data)
                        if calculated_subtotal > 0:
                            # Proportional discount: (order_discount / total_gross) × item_gross
                            distributed_order_discount = (remaining_order_discount / calculated_subtotal) * gross_amount
                            taxable_amount = gross_amount - distributed_order_discount
                        else:
                            distributed_order_discount = 0.0
                            taxable_amount = gross_amount
                    else:
                        distributed_order_discount = 0.0
                        taxable_amount = float(item.get("price", {}).get("unitPrice", 0.0)) * qty
                    
                # Use r_j/(1+R) formula for precise tax calculation
                total_tax_rate = self._get_total_tax_rate(item, "percent")  # Get sum of all tax rates
                if total_tax_rate > 0:
                    individual_rate_decimal = rate / 100.0
                    total_rate_decimal = total_tax_rate / 100.0
                    recomputed_item_tax = self._r(taxable_amount, individual_rate_decimal, total_rate_decimal)
                else:
                    recomputed_item_tax = 0.0
                recomputed_total += recomputed_item_tax
                
                # Prefer real Mongo _id if present. Some payloads may wrap _id like {"$oid": "..."}
                raw_oid = item.get("_id")
                if isinstance(raw_oid, dict) and "$oid" in raw_oid:
                    raw_oid = raw_oid["$oid"]
                preferred_id = raw_oid or item.get("_id") or item.get("internalId") or item.get("id")
                items.append({
                    "name": item.get("name", "Unknown"),
                    "item_id": preferred_id,
                    "internal_id": item.get("internalId"),
                    "qty": qty,
                    "unit_price": round(float(item.get("price", {}).get("unitPrice", 0.0)), 5),
                    "total_price": round(total_price, 5),
                    "item_discount": round(item_discount, 5),
                    "distributed_order_discount": round(distributed_order_discount, 5),
                    "taxable_amount": round(taxable_amount, 5),
                    "expected": expected_item_tax,  # Exact DB value
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
                        # For modifiers in Pattern 2, use the modifier's totalPrice as basis
                        # since modifiers don't have separate unitPrice × qty structure like items
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
                    
                # Use r_j/(1+R) formula for precise modifier tax calculation
                mod_total_tax_rate = self._get_total_tax_rate(mod, "percent")  # Get sum of all tax rates
                if mod_total_tax_rate > 0:
                    individual_rate_decimal = rate / 100.0
                    total_rate_decimal = mod_total_tax_rate / 100.0
                    base_tax = self._r(mod_taxable_amount, individual_rate_decimal, total_rate_decimal)
                else:
                    base_tax = 0.0
                final_tax = base_tax * qty
                
                # Only add to recomputed_total if there's actual expected tax
                if expected_mod_tax > 0:
                    recomputed_total += final_tax
                
                modifiers.append({
                    "name": mod.get("name", "Unknown"),
                    "modifier_id": mod.get("internalId") or mod.get("_id") or mod.get("id"),
                    "parent_item": item.get("name", "Unknown"),
                    "parent_item_id": item.get("internalId") or item.get("_id") or item.get("id"),
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
        """
        Calculate tax from inclusive amount with precision control.
        
        This method provides backward compatibility for existing code while
        maintaining precision consistency. For new calculations, prefer the
        enhanced _r() method with r_j/(1+R) formula.
        
        Args:
            total_price: Tax-inclusive amount
            rate_percent: Tax rate as percentage (e.g., 5.0 for 5%)
            
        Returns:
            Tax amount rounded to specified precision
        """
        if rate_percent <= 0 or total_price <= 0:
            return 0.0
        rate = rate_percent / 100.0
        tax = total_price - (total_price / (1.0 + rate))
        return round(tax, self.precision)

    def _validate_discount_consistency(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Enhanced discount pattern validation with precision-aware tolerance handling.
        
        This method determines the correct discount pattern (1-4) and validates consistency
        between item-level and order-level discounts with improved tolerance thresholds.
        
        Returns a dictionary with:
        - is_valid: Boolean indicating if discounts are consistent
        - pattern: The determined discount pattern (corrected if needed)
        - pattern_code: Numeric pattern code (1-4) for programmatic use
        - warning: Warning message if inconsistent
        - item_discounts: Total item-level discounts
        - order_discount: Order-level discount
        - tolerance_used: The tolerance threshold applied
        - corrected_pattern: True if pattern was corrected from initial determination
        """
        has_item_discounts = self._has_item_level_discounts(order_data)
        order_discount = self._get_order_level_discount(order_data)
        item_discounts = self._get_total_item_level_discounts(order_data)
        
        # Precision-aware tolerance: use 1/precision_multiplier as base tolerance
        base_tolerance = 1.0 / self._precision_multiplier
        relative_tolerance = 0.05  # 5% relative tolerance for pattern reclassification
        
        # Initial pattern determination
        if has_item_discounts and order_discount > base_tolerance:
            initial_pattern = "Pattern 4: Combined (Item + Order Level Discounts)"
            pattern_code = 4
        elif has_item_discounts:
            initial_pattern = "Pattern 3: Item-Level Discounts Only"
            pattern_code = 3
        elif order_discount > base_tolerance:
            initial_pattern = "Pattern 2: Order-Level Discount Only"
            pattern_code = 2
        else:
            initial_pattern = "Pattern 1: No Discounts"
            pattern_code = 1
            
        # Start with pattern matching the initial determination
        pattern = initial_pattern
        final_pattern_code = pattern_code
        is_valid = True
        warning = None
        corrected_pattern = False
        tolerance_used = base_tolerance
        
        # Enhanced Pattern 4 → Pattern 3 reclassification logic
        if pattern_code == 4:
            discount_diff = abs(order_discount - item_discounts)
            # Use both absolute and relative tolerance for robust classification
            if (discount_diff <= base_tolerance or 
                (item_discounts > base_tolerance and discount_diff / item_discounts < relative_tolerance)):
                
                pattern = "Pattern 3: Item-Level Discounts Only"
                final_pattern_code = 3
                corrected_pattern = True
                tolerance_used = max(base_tolerance, discount_diff)
                warning = (
                    f"RECLASSIFIED: Pattern 4 → Pattern 3 (discount duplication detected)\n"
                    f"Order discount ({order_discount:.{self.precision}f}) ≈ item discounts ({item_discounts:.{self.precision}f})\n"
                    f"Difference: {discount_diff:.{self.precision}f} (within tolerance: {tolerance_used:.{self.precision}f})"
                )
        
        # Enhanced Pattern 3 validation with precision-aware thresholds
        elif pattern_code == 3 and order_discount > base_tolerance:
            discount_diff = abs(order_discount - item_discounts)
            
            if discount_diff <= base_tolerance:
                # Acceptable redundancy - order discount equals item discounts
                warning = (
                    f"REDUNDANCY: Pattern 3 with redundant order discount\n"
                    f"Order discount ({order_discount:.{self.precision}f}) = item discounts ({item_discounts:.{self.precision}f})\n"
                    f"Consider removing redundant order discount for cleaner data"
                )
            elif discount_diff / max(item_discounts, base_tolerance) < relative_tolerance:
                # Within relative tolerance - likely rounding differences
                tolerance_used = discount_diff
                warning = (
                    f"TOLERANCE: Pattern 3 with minor order discount variance\n"
                    f"Order discount ({order_discount:.{self.precision}f}) vs item discounts ({item_discounts:.{self.precision}f})\n"
                    f"Difference: {discount_diff:.{self.precision}f} (within relative tolerance: {relative_tolerance*100:.1f}%)"
                )
            else:
                # Significant inconsistency
                is_valid = False
                warning = (
                    f"ERROR: Pattern 3 discount calculation inconsistency\n"
                    f"Item-level discounts: {item_discounts:.{self.precision}f}\n"
                    f"Order-level discount: {order_discount:.{self.precision}f}\n"
                    f"Difference: {discount_diff:.{self.precision}f} exceeds tolerance ({base_tolerance:.{self.precision}f})\n"
                    f"This indicates a potential data integrity issue"
                )
        
        return {
            "is_valid": is_valid,
            "pattern": pattern,
            "pattern_code": final_pattern_code,
            "warning": warning,
            "item_discounts": round(item_discounts, self.precision),
            "order_discount": round(order_discount, self.precision),
            "tolerance_used": tolerance_used,
            "corrected_pattern": corrected_pattern,
            "initial_pattern": initial_pattern,
            "initial_pattern_code": pattern_code
        }
    
    def diagnose_tax_failures(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Diagnose common causes of taxAmount failures in menuDetails.taxes[i].
        
        This method analyzes the order data to identify specific issues that cause
        tax calculation mismatches and provides actionable debugging information.
        
        Returns:
            Dictionary containing diagnostic results and recommendations
        """
        diagnosis = {
            "issues_found": [],
            "warnings": [],
            "recommendations": [],
            "data_quality": {},
            "calculation_details": {}
        }
        
        # 1. Check for missing tax rate information
        menu_tax_ids = set()
        order_tax_ids = set()
        
        # Collect all tax IDs from menuDetails
        for item in order_data.get("menuDetails", []):
            for tax_info in item.get("taxes", []):
                if "taxId" in tax_info:
                    menu_tax_ids.add(str(tax_info["taxId"]))
            for mod in item.get("extraDetails", []):
                for tax_info in mod.get("taxes", []):
                    if "taxId" in tax_info:
                        menu_tax_ids.add(str(tax_info["taxId"]))
        
        # Collect all tax IDs from orderTaxes
        for order_tax in order_data.get("orderTaxes", []):
            tax_id = str(order_tax.get("_id", order_tax.get("taxId", "")))
            if tax_id:
                order_tax_ids.add(tax_id)
        
        # Find missing rates
        missing_rates = menu_tax_ids - order_tax_ids
        if missing_rates:
            diagnosis["issues_found"].append({
                "type": "missing_tax_rates",
                "description": "Tax IDs found in menuDetails but missing in orderTaxes",
                "tax_ids": list(missing_rates),
                "severity": "HIGH",
                "impact": "Tax calculations will return 0.0 for these tax IDs"
            })
        
        # 2. Check discount pattern consistency
        try:
            pattern_info = self._validate_discount_consistency(order_data)
            if not pattern_info["is_valid"]:
                diagnosis["issues_found"].append({
                    "type": "discount_pattern_inconsistency",
                    "description": "Discount allocation pattern is inconsistent",
                    "pattern": pattern_info["pattern"],
                    "warning": pattern_info.get("warning"),
                    "severity": "MEDIUM",
                    "impact": "Taxable amounts may be calculated incorrectly"
                })
            
            if pattern_info.get("corrected_pattern"):
                diagnosis["warnings"].append({
                    "type": "pattern_reclassification",
                    "description": f"Pattern auto-corrected: {pattern_info['initial_pattern']} → {pattern_info['pattern']}",
                    "reason": "Discount duplication detected"
                })
        except Exception as e:
            diagnosis["issues_found"].append({
                "type": "pattern_detection_error",
                "description": f"Failed to analyze discount pattern: {str(e)}",
                "severity": "HIGH"
            })
        
        # 3. Analyze data quality issues
        total_items = 0
        items_with_taxes = 0
        empty_tax_arrays = 0
        missing_amounts = 0
        zero_amounts = 0
        
        for item in order_data.get("menuDetails", []):
            total_items += 1
            taxes = item.get("taxes", [])
            
            if not taxes:
                empty_tax_arrays += 1
            else:
                items_with_taxes += 1
                for tax_info in taxes:
                    amount = tax_info.get("amount")
                    if amount is None:
                        missing_amounts += 1
                    elif amount == 0.0:
                        zero_amounts += 1
        
        diagnosis["data_quality"] = {
            "total_items": total_items,
            "items_with_taxes": items_with_taxes,
            "empty_tax_arrays": empty_tax_arrays,
            "missing_tax_amounts": missing_amounts,
            "zero_tax_amounts": zero_amounts,
            "tax_coverage_percent": round((items_with_taxes / max(total_items, 1)) * 100, 2)
        }
        
        # Flag quality issues
        if empty_tax_arrays > 0:
            diagnosis["warnings"].append({
                "type": "empty_tax_arrays",
                "description": f"{empty_tax_arrays} items have no tax assignments",
                "impact": "These items will not contribute to tax calculations"
            })
        
        # 4. Check for calculation variances
        precision_issues = []
        total_variance = 0.0
        
        try:
            for tax_id in menu_tax_ids:
                if tax_id in order_tax_ids:
                    expected = self._sum_expected_menu_tax(order_data, tax_id)
                    recomputed = self._recompute_menu_tax(order_data, tax_id)
                    variance = abs(expected - recomputed)
                    total_variance += variance
                    
                    if variance > 0:
                        # Classify variance severity
                        if variance >= 0.1:
                            severity = "HIGH"
                        elif variance >= 0.01:
                            severity = "MEDIUM"  
                        else:
                            severity = "LOW"
                            
                        precision_issues.append({
                            "tax_id": tax_id,
                            "expected": round(expected, self.precision),
                            "recomputed": round(recomputed, self.precision),
                            "variance": round(variance, self.precision + 2),
                            "severity": severity
                        })
        except Exception as e:
            diagnosis["issues_found"].append({
                "type": "calculation_error",
                "description": f"Error during tax calculation: {str(e)}",
                "severity": "HIGH"
            })
        
        if precision_issues:
            diagnosis["calculation_details"]["variances"] = precision_issues
            diagnosis["calculation_details"]["total_variance"] = round(total_variance, self.precision + 2)
            
            # Flag high variances
            high_variances = [p for p in precision_issues if p["severity"] == "HIGH"]
            if high_variances:
                diagnosis["issues_found"].append({
                    "type": "high_calculation_variance",
                    "description": "Significant differences between expected and recomputed taxes",
                    "affected_taxes": [p["tax_id"] for p in high_variances],
                    "severity": "HIGH",
                    "impact": "Tax calculations are likely incorrect"
                })
        
        # 5. Generate specific recommendations
        if missing_rates:
            diagnosis["recommendations"].append("🔧 Add missing tax rates to orderTaxes array")
            diagnosis["recommendations"].append(f"   Missing tax IDs: {', '.join(list(missing_rates)[:5])}")
        
        if empty_tax_arrays > 0:
            diagnosis["recommendations"].append(f"📋 Review {empty_tax_arrays} items with empty tax arrays - ensure tax categories are assigned")
        
        if pattern_info and pattern_info.get("corrected_pattern"):
            diagnosis["recommendations"].append("💰 Clean up redundant discount data to avoid pattern reclassification")
        
        if precision_issues:
            high_count = len([p for p in precision_issues if p["severity"] == "HIGH"])
            if high_count > 0:
                diagnosis["recommendations"].append(f"🎯 Investigate {high_count} tax calculation(s) with high variance")
            diagnosis["recommendations"].append(f"🔢 Test with higher precision: --precision {min(8, self.precision + 2)}")
        
        # 6. Add diagnostic commands for debugging
        diagnosis["debug_commands"] = [
            "# Test with enhanced precision",
            f"python -m smart_cal.cli verify-order --order-id <ORDER_ID> --precision {min(8, self.precision + 2)} --verbose",
            "",
            "# View detailed component breakdown", 
            "python -m smart_cal.cli verify-order --order-id <ORDER_ID> --tax-view full",
            "",
            "# Test precision sensitivity",
            "python -m smart_cal.cli verify-order --order-id <ORDER_ID> --precision 3",
            "python -m smart_cal.cli verify-order --order-id <ORDER_ID> --precision 8"
        ]
        
        return diagnosis
        
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
            
            # Generate enhanced component breakdown for detailed analysis
            component_breakdown = self._per_tax_component_breakdown(order_data, tax_id)
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
                    "component_breakdown": component_breakdown,
                    "enhanced_analysis": {
                        "precision_used": self.precision,
                        "formula": "r_j/(1+R)",
                        "pattern_applied": component_breakdown.get("pattern_info", {}).get("pattern", "Unknown"),
                        "validation_status": component_breakdown.get("validation", {}).get("is_valid", False)
                    }
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

        return {
            "comparisons": comparisons, 
            "summary": summary,
            "orderTaxes": order_data.get("orderTaxes", [])  # Include orderTaxes for CLI validation
        }

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
            # Prefer Mongo _id (ObjectId) over internalId for display; normalize {'$oid': ...} shape
            raw_oid = item_data.get("_id")
            if isinstance(raw_oid, dict) and "$oid" in raw_oid:
                raw_oid = raw_oid["$oid"]
            preferred_oid = raw_oid if raw_oid else None
            # Keep legacy internalId as fallback
            item_id = preferred_oid or item_data.get("internalId") or item_data.get("id") or "Unknown ID"
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
            
            # Fix for database inconsistency: if grossAmount is 0 but should be calculated
            if gross_amount == 0.0 and unit_price > 0:
                # Database might not store grossAmount, use calculated value for validation
                expected_gross_amount_for_validation = expected_gross_amount
            else:
                expected_gross_amount_for_validation = expected_gross_amount
            
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
                effective_gross_amount_tax_free = gross_amount if gross_amount > 0 else expected_gross_amount
                expected_total_price = effective_gross_amount_tax_free - discount_amount
                if discount_amount == 0:
                    total_price_formula = f"unitPrice*qty({effective_gross_amount_tax_free}) (tax-free, no discounts)"
                else:
                    total_price_formula = f"unitPrice*qty({effective_gross_amount_tax_free}) - discountAmount({discount_amount}) (tax-free)"
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

                # DECOUPLED TAX CALCULATION: Calculate taxable amount directly from core data
                # Don't depend on grossAmount/netAmount which might be incorrect in DB
                base_amount = unit_price * qty  # Always calculate from core data
                
                if "Pattern 1" in discount_context:
                    # No discounts: taxable = unitPrice × qty
                    taxable_amount = base_amount
                elif "Pattern 2" in discount_context:
                    # Order-level discount: proportional allocation
                    if global_unit_price > 0 and global_discount_inclusive > 0:
                        proportion = global_discount_inclusive / global_unit_price
                        allocated_discount = proportion * base_amount
                        taxable_amount = base_amount - allocated_discount
                    else:
                        taxable_amount = base_amount
                elif "Pattern 3" in discount_context:
                    # Item-level discount: subtract from base amount
                    taxable_amount = base_amount - discount_amount
                elif "Pattern 4" in discount_context:
                    # Combined: item-level + residual order-level allocation
                    # Step 1: Apply item-level discount
                    post_item_amount = base_amount - discount_amount
                    
                    # Step 2: Calculate residual order discount allocation
                    residual_order_discount = max(global_discount_inclusive - total_item_level_discount_sum, 0.0)
                    if residual_order_discount > 0:
                        post_item_total = max(global_unit_price - total_item_level_discount_sum, 0.0)
                        if post_item_total > 0:
                            allocated_residual = residual_order_discount * (post_item_amount / post_item_total)
                            taxable_amount = post_item_amount - allocated_residual
                        else:
                            taxable_amount = post_item_amount
                    else:
                        taxable_amount = post_item_amount
                else:
                    # Fallback: base amount minus any discount
                    taxable_amount = base_amount - discount_amount
                
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
                # Use effective gross amount for calculation when DB grossAmount is 0
                effective_gross_amount = gross_amount if gross_amount > 0 else expected_gross_amount
                expected_total_price = effective_gross_amount - discount_amount
                if discount_amount == 0:
                    total_price_formula = f"unitPrice*qty({effective_gross_amount}) (no discounts)"
                else:
                    total_price_formula = f"unitPrice*qty({effective_gross_amount}) - discountAmount({discount_amount})"
            
            # Use strict tolerance for all fields - fail anything that doesn't match expected
            pattern_adjusted_tolerance = tolerance
            tax_strict_tolerance = tolerance  # Always enforce strict tolerance for taxAmount
            
            # Validate calculations with pattern-aware tolerance
            item_validation = {
                "_id": preferred_oid,  # expose canonical Mongo _id for CLI preference
                "item_id": item_id,    # retain for backward compatibility / fallback
                "item_name": item_name,
                "item_type": item_type,
                "qty": qty,
                "tax_rate": total_tax_rate,
                "calculations": {},
                "pattern_context": discount_context
            }
            
            # Check each calculation with strict tolerance - fail anything that doesn't match expected
            gross_tolerance = tolerance
            gross_formula = f"unitPrice({unit_price}) × qty({qty})"
            if gross_amount == 0.0 and expected_gross_amount > 0:
                # Database doesn't store grossAmount - this should fail validation
                gross_formula += " (DB stores 0, expected > 0 - VALIDATION FAILURE)"
            
            calculations = [
                ("grossAmount", gross_amount, expected_gross_amount, gross_formula, gross_tolerance),
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
            """Return a stable identifier preferring Mongo _id over internalId.

            Order of precedence:
              1. _id (supports {'$oid': '...'} shape)
              2. internalId
              3. id
              4. NAME::name fallback (non‑unique)
            """
            raw_oid = obj.get("_id")
            if isinstance(raw_oid, dict) and "$oid" in raw_oid:
                raw_oid = raw_oid["$oid"]
            if raw_oid not in (None, ""):
                return str(raw_oid)
            for k in ("internalId", "id"):
                if k in obj and obj.get(k) not in (None, ""):
                    return str(obj.get(k))
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
                # Prefer modifier _id similarly
                raw_mod_oid = mod.get("_id")
                if isinstance(raw_mod_oid, dict) and "$oid" in raw_mod_oid:
                    raw_mod_oid = raw_mod_oid["$oid"]
                if raw_mod_oid not in (None, ""):
                    mod_id = str(raw_mod_oid)
                else:
                    for k in ("internalId", "id"):
                        if k in mod and mod.get(k) not in (None, ""):
                            mod_id = str(mod.get(k))
                            break
                    else:
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
                raw_mod_oid = mod.get("_id")
                if isinstance(raw_mod_oid, dict) and "$oid" in raw_mod_oid:
                    raw_mod_oid = raw_mod_oid["$oid"]
                if raw_mod_oid not in (None, ""):
                    mod_id = str(raw_mod_oid)
                else:
                    for k in ("internalId", "id"):
                        if k in mod and mod.get(k) not in (None, ""):
                            mod_id = str(mod.get(k))
                            break
                    else:
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
            # Extract canonical _id for display if present
            raw_display_oid = m_entry.get("_id")
            if isinstance(raw_display_oid, dict) and "$oid" in raw_display_oid:
                raw_display_oid = raw_display_oid["$oid"]
            item_detail = {
                "key": key,
                "_id": raw_display_oid if raw_display_oid not in (None, "") else None,
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
