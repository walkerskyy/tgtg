from threading import Thread

from kivy.app import App
from kivy.clock import mainthread
from kivy.uix.screenmanager import Screen
from kivy.properties import ListProperty, BooleanProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.image import AsyncImage


from models.item import Item


class ItemCard(BoxLayout):
    def __init__(self, item: Item, on_tap=None, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "horizontal"
        self.size_hint_y = None
        self.height = 150
        self.padding = 15
        self.spacing = 15

        self.item = item
        self.on_tap = on_tap

        with self.canvas.before:
            from kivy.graphics import Color, RoundedRectangle
            Color(0.2, 0.22, 0.28, 1)
            self.rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[8])

        self.bind(pos=self.update_rect, size=self.update_rect)
        self._build_ui()

    def update_rect(self, *args):
        self.rect.pos = self.pos
        self.rect.size = self.size

    def _build_ui(self):
        self.clear_widgets()

        img = AsyncImage(
            source=self.item.item_logo,
            size_hint_x=None,
            width=130,
            allow_stretch=True,
            keep_ratio=True,
        )
        self.add_widget(img)

        info = BoxLayout(orientation="vertical", size_hint_x=1, size_hint_y=1, padding=10, spacing=6)
        
        name_label = Label(
            text=self.item.display_name,
            font_size="18sp",
            bold=True,
            color=(1, 1, 1, 1),
            size_hint_y=0.6,
            valign="middle",
            halign="left",
            shorten=True,
            shorten_from="right",
        )
        
        price_label = Label(
            text=f"{self.item.price} (was {self.item.value})",
            font_size="15sp",
            color=(0.8, 0.8, 0.8, 1),
            size_hint_y=0.2,
            valign="middle",
            halign="left",
        )

        avail_label = Label(
            text=f"{self.item.items_available} bags available" if self.item.is_available else "Sold out",
            font_size="14sp",
            color=(0.29, 0.76, 0.26, 1) if self.item.is_available else (0.9, 0.3, 0.3, 1),
            size_hint_y=0.2,
            valign="middle",
            halign="left",
        )

        info.add_widget(name_label)
        info.add_widget(price_label)
        info.add_widget(avail_label)
        self.add_widget(info)


class FavoritesScreen(Screen):
    items = ListProperty([])
    is_loading = BooleanProperty(False)
    error_message = StringProperty("")
    _previous_item_ids = set()
    _fetch_thread = None

    def on_enter(self):
        self.load_favorites()

    def on_leave(self):
        if self._fetch_thread and self._fetch_thread.is_alive():
            pass

    def load_favorites(self):
        if self.is_loading:
            return

        app = App.get_running_app()
        if not app or not app.tgtg_client:
            self.error_message = "Not logged in"
            return

        self.is_loading = True
        self.error_message = ""

        previous_ids = self._previous_item_ids.copy()

        def fetch():
            try:
                import logging
                log = logging.getLogger("tgtg_mobile")
                
                favorites = app.tgtg_client.get_favorites()
                log.info(f"Got {len(favorites)} favorites from API")
                items = [Item(f) for f in favorites]
                log.info(f"Created {len(items)} Item objects")
                
                current_item_ids = {i.item_id for i in items}
                log.info(f"Found {len(current_item_ids)} total items")
                
                available_items = [i for i in items if i.is_available]
                for item in available_items:
                    if item.item_id not in previous_ids:
                        notif_mgr = getattr(app, "notification_manager", None) or getattr(app, "notification_service", None)
                        if notif_mgr:
                            notif_mgr.send_bag_alert(item)
                
                self._update_previous_items(current_item_ids)
                self._update_items(items)
            except Exception as e:
                self._show_error(str(e))
            finally:
                self._set_loading_done()

        self._fetch_thread = Thread(target=fetch, daemon=True)
        self._fetch_thread.start()

    @mainthread
    def _update_previous_items(self, item_ids):
        self._previous_item_ids = item_ids

    @mainthread
    def _set_loading_done(self):
        self.is_loading = False

    @mainthread
    def _update_items(self, items):
        self.items = items
        self._build_item_list()

    @mainthread
    def _show_error(self, error):
        self.error_message = error

    def _build_item_list(self):
        container = self.ids.items_container
        container.clear_widgets()

        if not self.items:
            container.add_widget(Label(
                text="No favorites found\nAdd favorites in the TGTG app",
                font_size="14sp",
                color=(0.7, 0.7, 0.7, 1),
                halign="center",
            ))
            return

        for item in self.items:
            card = ItemCard(item, on_tap=lambda i: self.show_item_detail(i))
            container.add_widget(card)

    def show_item_detail(self, item: Item):
        detail_screen = self.manager.get_screen("item_detail")
        detail_screen.set_item(item)
        self.manager.current = "item_detail"

    def refresh(self):
        self.load_favorites()
