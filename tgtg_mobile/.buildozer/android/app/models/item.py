import datetime


class Item:
    def __init__(self, data: dict, locale: str = "en_US", time_format: str = "24h"):
        self.items_available: int = data.get("items_available", 0)
        self.display_name: str = data.get("display_name", "-")
        self.favorite: bool = data.get("favorite", False)
        self.pickup_interval_start: str | None = data.get("pickup_interval", {}).get("start")
        self.pickup_interval_end: str | None = data.get("pickup_interval", {}).get("end")
        self.pickup_location: str = data.get("pickup_location", {}).get("address", {}).get("address_line", "-")

        item: dict = data.get("item", {})
        self.item_id: str = item.get("item_id", "")
        self._rating: float | None = item.get("average_overall_rating", {}).get("average_overall_rating")
        self.packaging_option: str = item.get("packaging_option", "-")
        self.item_name: str = item.get("name", "-")
        self.buffet: bool = item.get("buffet", False)
        self.item_category: str = item.get("item_category", "-")
        self.description: str = item.get("description", "-")

        item_price: dict = item.get("item_price", {})
        item_value: dict = item.get("item_value", {})
        self._price: float = item_price.get("minor_units", 0) / 10 ** max(1, item_price.get("decimals", 2))
        self._value: float = item_value.get("minor_units", 0) / 10 ** max(1, item_value.get("decimals", 2))
        self.currency: str = item_price.get("code", "EUR")

        self.item_logo: str = item.get("logo_picture", {}).get(
            "current_url",
            "https://tgtg-mkt-cms-prod.s3.eu-west-1.amazonaws.com/13512/TGTG_Icon_White_Cirle_1988x1988px_RGB.png",
        )
        self.item_cover: str = item.get("cover_picture", {}).get(
            "current_url",
            "https://images.tgtg.ninja/standard_images/GENERAL/other1.jpg",
        )

        store: dict = data.get("store", {})
        self.store_name: str = store.get("store_name", "-")
        self.store_id: str = store.get("store_id", "")

        self.locale = locale
        self.time_format = time_format

    @property
    def rating(self) -> str:
        if self._rating is None:
            return "-"
        return f"{self._rating:.1f}"

    @property
    def price(self) -> str:
        if self.currency == "EUR":
            return f"€{self._price:.2f}"
        return f"{self._price:.2f} {self.currency}"

    @property
    def value(self) -> str:
        if self.currency == "EUR":
            return f"€{self._value:.2f}"
        return f"{self._value:.2f} {self.currency}"

    @property
    def discount(self) -> int:
        if self._value > 0:
            return int((1 - self._price / self._value) * 100)
        return 0

    @property
    def is_available(self) -> bool:
        return self.items_available > 0

    @property
    def pickupdate(self) -> str:
        if not self.pickup_interval_start or not self.pickup_interval_end:
            return "-"
        try:
            fmt = "%Y-%m-%dT%H:%M:%SZ"
            pfr = datetime.datetime.strptime(self.pickup_interval_start, fmt)
            pto = datetime.datetime.strptime(self.pickup_interval_end, fmt)
            pfr = pfr.replace(tzinfo=datetime.timezone.utc).astimezone()
            pto = pto.replace(tzinfo=datetime.timezone.utc).astimezone()

            if self.time_format == "12h":
                prange = f"{pfr.strftime('%I:%M %p')} - {pto.strftime('%I:%M %p')}"
            else:
                prange = f"{pfr.hour:02d}:{pfr.minute:02d} - {pto.hour:02d}:{pto.minute:02d}"

            now = datetime.datetime.now()
            if now.date() == pfr.date():
                return f"Today, {prange}"
            tomorrow = now + datetime.timedelta(days=1)
            if (pfr.date() - now.date()).days == 1:
                return f"Tomorrow, {prange}"
            return f"{pfr.day}/{pfr.month}, {prange}"
        except (ValueError, TypeError):
            return "-"

    @property
    def link(self) -> str:
        return f"https://share.toogoodtogo.com/item/{self.item_id}"

    @classmethod
    def from_dict(cls, data: dict) -> "Item":
        return cls(data)
