from threading import Thread

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
        self.height = "90dp"
        self.padding = 10
        self.spacing = 10

        self.item = item
        self.on_tap = on_tap

        self._build_ui()

    def _build_ui(self):
        self.clear_widgets()

        img = AsyncImage(
            source=self.item.item_logo,
            size_hint_x = None,
            width = "80dp",
            allow_stretch = True,
            keep_ratio = True,
        )
        self.add_widget(img)

        info = BoxLayout(orientation="vertical", size_hint_x=1)
        
        name_label = Label(
            text=self.item.display_name,
            font_size="14sp",
            bold=True,
            color=(1, 1, 1, 1),
            size_hint_y=0.5,
            valign="bottom",
            text_size=(self.width - 20, None),
            shorten=True,
            shorten_from="right",
        )
        
        price_label = Label(
            text=f"{self.item.price} (was {self.item.value})",
            font_size="12sp",
            color=(0.7, 0.7, 0.7, 1),
            size_hint_y=0.3,
            valign="middle",
        )

        avail_label = Label(
            text=f"{self.item.items_available} bags available" if self.item.is_available else "Sold out",
            font_size="11sp",
            color=(0.29, 0.76, 0.26, 1) if self.item.is_available else (0.9, 0.3, 0.3, 1),
            size_hint_y=0.2,
            valign="top",
        )

        info.add_widget(name_label)
        info.add_widget(price_label)
        info.add_widget(avail_label)
        self.add_widget(info)


class FavoritesScreen(Screen):
    items = ListProperty([])
    is_loading = BooleanProperty(False)
    error_message = StringProperty("")
    _previous_items = set()

    def on_enter(self):
        self.load_favorites()

    def load_favorites(self):
        if self.is_loading:
            return

        app = self.manager.app
        if not app or not app.tgtg_client:
            self.error_message = "Not logged in"
            return

        self.is_loading = True
        self.error_message = ""

        def fetch():
            try:
                favorites = app.tgtg_client.get_favorites()
                items = [Item(f) for f in favorites]
                
                current_item_ids = {i.item_id for i in items}
                available_items = [i for i in items if i.is_available]
                
                for item in available_items:
                    if item.item_id not in self._previous_items and self._previous_items:
                        if app.notification_service:
                            app.notification_service.send_notification(
                                title="TGTG Available!",
                                message=f"{item.display_name} - {item.price}",
                                item_id=item.item_id
                            )
                
                self._previous_items = current_item_ids
                self._update_items(items)
            except Exception as e:
                self._show_error(str(e))
            finally:
                self.is_loading = False

        Thread(target=fetch, daemon=True).start()

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
