#!/usr/bin/env python3
"""Test script to verify the mobile app works on desktop."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_imports():
    print("Testing imports...")
    from tgtg_mobile.models.item import Item
    print("  ✓ Item model")
    
    from tgtg_mobile.tgtg_api.client import TgtgClient
    print("  ✓ TgtgClient")
    
    from tgtg_mobile.errors import TgtgAPIError, TgtgLoginError
    print("  ✓ Error classes")
    
    from tgtg_mobile.services.token_storage import TokenStorage
    print("  ✓ TokenStorage")
    
    return True

def test_item_model():
    print("\nTesting Item model...")
    from tgtg_mobile.models.item import Item
    
    test_data = {
        "display_name": "Test Store",
        "items_available": 5,
        "favorite": True,
        "pickup_interval": {
            "start": "2024-01-04T19:00:00Z",
            "end": "2024-01-04T19:30:00Z"
        },
        "pickup_location": {
            "address": {"address_line": "123 Test St"}
        },
        "item": {
            "item_id": "123",
            "name": "Magic Bag",
            "description": "Test description",
            "item_price": {"minor_units": 350, "decimals": 2, "code": "EUR"},
            "item_value": {"minor_units": 900, "decimals": 2, "code": "EUR"},
            "average_overall_rating": {"average_overall_rating": 4.5},
            "logo_picture": {"current_url": "https://example.com/logo.png"},
            "cover_picture": {"current_url": "https://example.com/cover.png"},
        },
        "store": {
            "store_name": "Test Store",
            "store_id": "456"
        }
    }
    
    item = Item(test_data)
    
    assert item.display_name == "Test Store", f"Expected 'Test Store', got '{item.display_name}'"
    assert item.items_available == 5
    assert item.is_available == True
    assert "3.50" in item.price
    assert "9.00" in item.value
    assert item.discount == 61  # ~61% off
    
    print("  ✓ Item parsing")
    print(f"  ✓ Price: {item.price}")
    print(f"  ✓ Discount: {item.discount}%")
    
    return True

def test_kivy():
    print("\nTesting Kivy components...")
    os.environ['KIVY_NO_ARGS'] = '1'
    os.environ['KIVY_NO_CONSOLELOG'] = '1'
    
    from kivy.app import App
    print("  ✓ Kivy App base")
    
    from tgtg_mobile.main import TgtgMobileApp
    print("  ✓ TgtgMobileApp")
    
    return True

def main():
    print("=" * 50)
    print("TGTG Mobile App - Desktop Test")
    print("=" * 50)
    
    tests = [
        ("Imports", test_imports),
        ("Item Model", test_item_model),
        ("Kivy", test_kivy),
    ]
    
    passed = 0
    failed = 0
    
    for name, test_func in tests:
        try:
            if test_func():
                passed += 1
                print(f"\n✓ {name} - PASSED")
            else:
                failed += 1
                print(f"\n✗ {name} - FAILED")
        except Exception as e:
            failed += 1
            print(f"\n✗ {name} - FAILED: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 50)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 50)
    
    return failed == 0

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
