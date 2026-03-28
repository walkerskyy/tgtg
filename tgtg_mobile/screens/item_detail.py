from kivy.uix.screenmanager import Screen
from kivy.properties import ObjectProperty, StringProperty

from models.item import Item


class ItemDetailScreen(Screen):
    item = ObjectProperty(None, allownone=True)

    def set_item(self, item: Item):
        self.item = item
        self._update_ui()

    def _update_ui(self):
        if not self.item:
            return

        self.ids.store_name.text = self.item.display_name
        self.ids.item_price.text = self.item.price
        self.ids.item_value.text = f"Value: {self.item.value}"
        self.ids.discount.text = f"{self.item.discount}% OFF"
        self.ids.item_description.text = self.item.description or "No description available"
        self.ids.pickup_time.text = self.item.pickupdate
        self.ids.pickup_location.text = self.item.pickup_location
        self.ids.availability.text = f"{self.item.items_available} bags available"
        self.ids.availability.color = (0.29, 0.76, 0.26, 1) if self.item.is_available else (0.9, 0.3, 0.3, 1)
        self.ids.item_image.source = self.item.item_cover
        self.ids.item_logo.source = self.item.item_logo

        if self.item.rating != "-":
            self.ids.rating.text = f"⭐ {self.item.rating}"
            self.ids.rating.opacity = 1
        else:
            self.ids.rating.opacity = 0

    def toggle_favorite(self):
        if not self.item or not self.manager.app or not self.manager.app.tgtg_client:
            return

        new_state = not self.item.favorite
        try:
            self.manager.app.tgtg_client.set_favorite(self.item.item_id, new_state)
            self.item.favorite = new_state
            self.ids.favorite_btn.text = "♥" if new_state else "♡"
            self.ids.favorite_btn.color = (0.9, 0.3, 0.3, 1) if new_state else (0.7, 0.7, 0.7, 1)
        except Exception as e:
            pass

    def go_back(self):
        self.manager.current = "favorites"
