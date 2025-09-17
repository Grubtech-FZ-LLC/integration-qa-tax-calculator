"""
Script to extract discount information from a specific order.
"""
import sys
import os
import json
from pymongo import MongoClient
from dotenv import load_dotenv
import pprint

def main():
    # Load environment variables
    load_dotenv()
    
    # Check arguments
    if len(sys.argv) < 2:
        print("Usage: python extract_discounts.py <order_id> [environment]")
        sys.exit(1)
    
    order_id = sys.argv[1]
    environment = sys.argv[2] if len(sys.argv) > 2 else "production"
    
    # Map environment
    env_map = {
        "staging": "GRUBTECH_MASTER_DATA_STG_V2",
        "stg": "GRUBTECH_MASTER_DATA_STG_V2", 
        "production": "GRUBTECH_MASTER_DATA_PROD_V2",
        "prod": "GRUBTECH_MASTER_DATA_PROD_V2"
    }
    db_name = env_map.get(environment.lower(), "GRUBTECH_MASTER_DATA_PROD_V2")
    
    # Connect to MongoDB
    connection_url = os.getenv("DB_CONNECTION_URL")
    if not connection_url:
        print("DB_CONNECTION_URL environment variable not set")
        sys.exit(1)
    
    client = MongoClient(connection_url)
    db = client[db_name]
    collection = db["PARTNER_RESTAURANT_ORDER"]
    
    # Find the order
    order = collection.find_one({"internalId": order_id})
    if not order:
        print(f"Order with ID {order_id} not found in {db_name}")
        sys.exit(1)
    
    # Extract discount information
    print(f"=== DISCOUNT ANALYSIS FOR ORDER {order_id} ===")
    
    # Order-level discount
    payment_details = order.get("paymentDetails", {})
    price_details = payment_details.get("priceDetails", {})
    order_discount = float(price_details.get("discountAmount", 0.0))
    order_unit_price = float(price_details.get("unitPrice", 0.0))
    order_total_price = float(price_details.get("totalPrice", 0.0))
    
    print(f"ORDER LEVEL:")
    print(f"  Unit Price: ${order_unit_price:.5f}")
    print(f"  Total Price: ${order_total_price:.5f}")
    print(f"  Discount Amount: ${order_discount:.5f}")
    
    # Item-level discounts
    item_discounts = 0.0
    item_total = 0.0
    mod_discounts = 0.0
    mod_total = 0.0
    
    print(f"\nITEM LEVEL DISCOUNTS:")
    for item in order.get("menuDetails", []):
        item_name = item.get("name", "Unknown")
        item_qty = int(item.get("qty", 1))
        item_unit_price = float(item.get("price", {}).get("unitPrice", 0.0))
        item_total_price = float(item.get("price", {}).get("totalPrice", 0.0))
        item_discount = float(item.get("price", {}).get("discountAmount", 0.0))
        
        item_discounts += item_discount
        item_total += item_total_price
        
        if item_discount > 0:
            print(f"  {item_name} (x{item_qty}):")
            print(f"    Unit Price: ${item_unit_price:.5f}")
            print(f"    Total Price: ${item_total_price:.5f}")
            print(f"    Discount: ${item_discount:.5f}")
        
        # Check modifiers
        for mod in item.get("extraDetails", []):
            mod_name = mod.get("name", "Unknown")
            mod_unit_price = float(mod.get("price", {}).get("unitPrice", 0.0))
            mod_total_price = float(mod.get("price", {}).get("totalPrice", 0.0))
            mod_discount = float(mod.get("price", {}).get("discountAmount", 0.0))
            
            # Apply item quantity to modifier discount
            effective_mod_discount = mod_discount * item_qty
            mod_discounts += effective_mod_discount
            mod_total += mod_total_price
            
            if mod_discount > 0:
                print(f"    Modifier: {mod_name} (x{item_qty}):")
                print(f"      Unit Price: ${mod_unit_price:.5f}")
                print(f"      Total Price: ${mod_total_price:.5f}")
                print(f"      Discount per unit: ${mod_discount:.5f}")
                print(f"      Effective Discount: ${effective_mod_discount:.5f}")
    
    total_item_discounts = item_discounts + mod_discounts
    print(f"\nSUMMARY:")
    print(f"  Item Discounts: ${item_discounts:.5f}")
    print(f"  Modifier Discounts: ${mod_discounts:.5f}")
    print(f"  Total Item-Level Discounts: ${total_item_discounts:.5f}")
    print(f"  Order-Level Discount: ${order_discount:.5f}")
    print(f"  Combined Discount: ${total_item_discounts + order_discount:.5f}")
    
    # Pattern determination
    has_item_discounts = total_item_discounts > 0
    
    if has_item_discounts and order_discount > 0:
        pattern = "Pattern 4: Combined (Item + Order Level Discounts)"
        remaining = max(0.0, order_discount - total_item_discounts)
        print(f"  Discount Pattern: {pattern}")
        print(f"  Remaining Order Discount: ${remaining:.5f}")
    elif has_item_discounts:
        pattern = "Pattern 3: Item-Level Discounts Only"
        print(f"  Discount Pattern: {pattern}")
    elif order_discount > 0:
        pattern = "Pattern 2: Order-Level Discount Only"
        print(f"  Discount Pattern: {pattern}")
    else:
        pattern = "Pattern 1: No Discounts"
        print(f"  Discount Pattern: {pattern}")
    
if __name__ == "__main__":
    main()