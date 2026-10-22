"""Turn the visible text of a checkout page into labelled price items (a 'waterfall')."""
from __future__ import annotations

import re

from app.capture.schema import PriceItem, PriceKind

_CUR = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR",
    "$": "USD", "us$": "USD", "usd": "USD",
    "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP", "¥": "JPY", "jpy": "JPY",
}

AMOUNT_RE = re.compile(
    r"(?P<neg>[-−–]\s?)?(?P<cur>₹|Rs\.?|INR|US\$|USD|\$|€|EUR|£|GBP|¥|JPY)\s?(?P<num>\d[\d,]*(?:\.\d{1,2})?)"
    r"|(?P<num2>\d[\d,]*(?:\.\d{1,2})?)\s?(?P<cur2>INR|USD|EUR|GBP|JPY)\b",
    re.IGNORECASE,
)

# Totals are often printed without a currency symbol ("Total: 360", or "Total" with "360" on the next line).
_TOTAL_WORDS = r"(?:grand\s+)?total|amount\s+payable|you\s+pay|to\s+pay|payable"
TOTAL_BARE = re.compile(rf"^(?P<label>{_TOTAL_WORDS})\s*:?\s*(?P<num>\d[\d,]*(?:\.\d{{1,2}})?)$", re.I)
BARE_NUM = re.compile(r"^\d[\d,]*(?:\.\d{1,2})?$")
TOTAL_LABEL = re.compile(rf"^(?:{_TOTAL_WORDS})\s*:?$", re.I)

_KINDS: list[tuple[PriceKind, re.Pattern]] = [
    # "Total savings" / "Total discount" / "Total MRP" are not what you pay: checked before the total rule.
    ("discount", re.compile(r"\btotal\s+(savings?|discounts?)\b|\byou\s+saved?\b", re.I)),
    ("base", re.compile(r"\btotal\s+(mrp|list\s+price)\b", re.I)),
    ("total", re.compile(r"\b(total|amount payable|you pay|grand total|payable|to pay)\b", re.I)),
    ("discount", re.compile(r"\b(discount|coupon|promo|voucher|savings?|you save)\b", re.I)),
    ("tax", re.compile(r"\b(tax|gst|vat|cess)\b", re.I)),
    ("addon", re.compile(r"\b(insurance|protect|protection|donation|tip|priority|add-?on|extra|upgrade|cover)\b", re.I)),
    ("fee", re.compile(r"\b(fee|fees|charge|charges|surcharge|handling|convenience|processing|service)\b", re.I)),
    ("base", re.compile(r"\b(ticket|fare|price|subtotal|item|room|seat|x\s?\d|×\s?\d|qty)\b", re.I)),
]


CURRENCY_CODE = re.compile(r"^(INR|USD|EUR|GBP|Rs\.?)$", re.I)


def classify_kind(label: str) -> PriceKind:
    for kind, rx in _KINDS:
        if rx.search(label):
            return kind
    return "other"


def parse_amounts(line: str) -> list[tuple[float, str, bool, tuple[int, int]]]:
    """All money amounts in a line: (value, currency, negative, span)."""
    out = []
    for m in AMOUNT_RE.finditer(line):
        num = (m.group("num") or m.group("num2")).replace(",", "")
        cur = _CUR.get((m.group("cur") or m.group("cur2")).lower(), "")
        try:
            val = float(num)
        except ValueError:
            continue
        out.append((val, cur, bool(m.groupdict().get("neg")), m.span()))
    return out


def extract_items(text: str) -> list[PriceItem]:
    """Labelled price items from page text. Handles 'Label  ₹80' and 'Label' / '₹80' on two lines."""
    lines = [ln.strip() for ln in text.replace("\t", "  ").splitlines() if ln.strip()]
    items: list[PriceItem] = []
    prev_label = ""
    for ln in lines:
        amts = parse_amounts(ln)
        if not amts:
            m = TOTAL_BARE.match(ln)
            if m or (BARE_NUM.match(ln) and TOTAL_LABEL.match(prev_label)):
                label, num = (m.group("label"), m.group("num")) if m else (prev_label.rstrip(" :"), ln)
                items.append(PriceItem(label=label, amount=float(num.replace(",", "")), kind="total"))
                prev_label = ""
                continue
            if not CURRENCY_CODE.match(ln):  # "Total" / "INR" / "₹6,999.00" (Shopify): the label is "Total"
                prev_label = ln
            continue
        val, cur, neg, (a, b) = amts[-1]
        label = (ln[:a] + " " + ln[b:]).strip(" :-–·|")
        if not label:
            label = prev_label  # amount on its own line: label was the previous line
        if len(label) > 90:
            continue  # prose, not a price row
        kind = classify_kind(label)
        if neg and kind in ("other", "base"):
            kind = "discount"
        if kind == "discount":
            val = -abs(val)
        items.append(PriceItem(label=label, amount=val, kind=kind, currency=cur))
        prev_label = ""
    return items


# Promotion banners and cart nudges quote thresholds, not the product's price ("Free Socks ₹1499+",
# "You're ₹421 away from free shipping", "Extra 10% off on orders above ₹999").
PROMO_LINE = re.compile(
    r"(₹|rs\.?|inr)\s?\d[\d,]*(\.\d+)?\s*\+|\baway from\b|\bunlock|\bfree\s+(shipping|delivery|gift|socks)\b|"
    r"\b(orders?|purchases?|shopping)\s+(above|over|of)\b|\b(above|over)\s+(₹|rs)|\bmin(imum)?\s+(order|purchase|cart)\b|"
    r"\buse\s+code\b|\bcoupon\b", re.I)
# "2 items", "Qty: 2", "Quantity 2", "Total items in cart: 2"
QTY_RE = re.compile(r"\b(\d{1,2})\s+items?\b|\bqty\s*:?\s*(\d{1,2})\b|\bquantity\s*:?\s*(\d{1,2})\b", re.I)


def cart_quantity(text: str) -> int | None:
    """Units in the cart as the page states it (largest stated count), or None if the page does not say."""
    counts = [int(next(g for g in m.groups() if g)) for m in QTY_RE.finditer(text)]
    counts = [c for c in counts if c > 0]
    return max(counts) if counts else None


def headline_price(text: str) -> tuple[float | None, str]:
    """First money amount on the page, skipping promotion thresholds: the price a shopper is shown first."""
    for ln in text.splitlines():
        if PROMO_LINE.search(ln):
            continue
        amts = parse_amounts(ln)
        if amts:
            return amts[0][0], amts[0][1]
    return None, ""


def final_total(items: list[PriceItem]) -> float | None:
    totals = [i for i in items if i.kind == "total"]
    if not totals:
        return None
    payable = [i for i in totals if re.search(r"payable|you pay|to pay", i.label, re.I)]
    return (payable or totals)[-1].amount


def drip_ratio(advertised: float | None, final: float | None) -> float | None:
    if not advertised or final is None or advertised <= 0:
        return None
    return (final - advertised) / advertised
