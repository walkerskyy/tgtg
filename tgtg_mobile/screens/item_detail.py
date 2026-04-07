from threading import Thread

from kivy.app import App
from kivy.clock import mainthread
from kivy.uix.screenmanager import Screen
from kivy.properties import ObjectProperty, StringProperty, BooleanProperty

from models.item import Item


class ItemDetailScreen(Screen):
    item = ObjectProperty(None, allownone=True)
    is_updating = BooleanProperty(False)

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

        self._update_favorite_button()

    def _update_favorite_button(self):
        if not self.item:
            return
        is_fav = self.item.favorite
        self.ids.favorite_btn.text = "♥" if is_fav else "♡"
        self.ids.favorite_btn.color = (0.9, 0.3, 0.3, 1) if is_fav else (0.7, 0.7, 0.7, 1)

    def toggle_favorite(self):
        app = App.get_running_app()
        if not self.item or not app or not app.tgtg_client:
            return
        if self.is_updating:
            return

        self.is_updating = True
        new_state = not self.item.favorite
        self.ids.favorite_btn.text = "..."
        self.ids.favorite_btn.color = (0.5, 0.5, 0.5, 1)

        def do_toggle():
            try:
                app.tgtg_client.set_favorite(self.item.item_id, new_state)
                self._on_toggle_success(new_state)
            except Exception as e:
                self._on_toggle_error(str(e))

        Thread(target=do_toggle, daemon=True).start()

    @mainthread
    def _on_toggle_success(self, new_state):
        self.item.favorite = new_state
        self.is_updating = False
        self._update_favorite_button()

    @mainthread
    def _on_toggle_error(self, error):
        self.is_updating = False
        self._update_favorite_button()

    def go_back(self):
        self.manager.current = "favorites"
