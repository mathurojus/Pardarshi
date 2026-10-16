"""Template-based synthetic training rows (offline, deterministic).

Slots (numbers, products, currencies, places) are filled randomly so the classifier learns the
*pattern* of each tactic rather than memorising a handful of strings. This is a second data
source next to the Gemini-generated corpus. NOTE for honesty: these templates are written by us,
so a model trained only on them would look better on our own test set than on the open web;
that is why the held-out set and the demo-shop ground truth are kept separate.
"""
from __future__ import annotations

import random

THINGS = ["room", "seat", "ticket", "table", "slot", "unit", "pair", "bed", "cabin", "spot", "kit", "pass"]
PLACES = ["Delhi", "Mumbai", "Pune", "Jaipur", "Goa", "Chennai", "Kolkata", "Lucknow", "Surat", "Bengaluru", "Hyderabad", "Kochi"]
NAMES = ["Aman", "Riya", "Karan", "Neha", "Vikram", "Sana", "Rohit", "Meera", "Arjun", "Isha", "Dev", "Pooja"]
EXTRAS = ["travel insurance", "priority boarding", "premium support", "extended warranty", "gift wrap", "SMS alerts",
          "express delivery", "fast-track entry", "extra legroom", "cancellation cover", "buyer protection", "a donation"]
VERBS = ["viewing", "looking at", "watching", "checking out", "booking"]
EVENTS = ["Summer Music Festival", "Tech Conference 2026", "Comedy Night", "Classical Evening", "Food Carnival",
          "Football Derby", "Film Premiere", "Dance Showcase", "Startup Summit", "Poetry Slam", "Cricket Finals"]
UNITS = ["minutes", "hours"]
CURS = ["Rs ", "INR ", "₹", "$", "€", "£"]


def _money(r: random.Random) -> str:
    return f"{r.choice(CURS)}{r.choice([9, 19, 29, 49, 79, 99, 149, 199, 249, 399, 499, 999])}"


def _clock(r: random.Random) -> str:
    return f"{r.randint(0, 59):02d}:{r.randint(0, 59):02d}"


# class -> (role, near_checked, templates)
TEMPLATES: dict[str, tuple[str, bool, list[str]]] = {
    "false_urgency": ("text", False, [
        "Offer ends in {clock}", "Hurry, this deal expires in {n} {unit}", "Price goes up in {n} {unit}!",
        "Book within {n} {unit} to keep this price", "Sale ends tonight at midnight", "Only {clock} left to claim this offer",
        "Limited-time deal, ends soon", "Your cart is reserved for {clock}", "Flash sale: {n}% off ends in {clock}",
        "Complete checkout in {clock} or the discount disappears", "This price is available for the next {n} {unit} only",
        "Last chance! Offer closing in {clock}", "Don't wait, today's rate won't last"]),
    "scarcity": ("text", False, [
        "Only {n} {thing}s left!", "Just {n} {thing} remaining at this price", "Almost sold out, {n} left",
        "Hot: only {n} {thing}s available", "Last {thing} at this rate", "{pct}% already booked",
        "Selling fast! {n} left in {place}", "Low availability: {n} {thing}s left", "Only {n} left in stock, order soon",
        "Few {thing}s left for your dates", "We're nearly full in {place}", "Grab it before it's gone, {n} {thing}s left"]),
    "fake_social_proof": ("text", False, [
        "{n} people are {verb} this right now", "{name} from {place} just booked this", "{n} bought this in the last hour",
        "{n} others are {verb} this {thing}", "Trending in {place}: booked {n} times today", "{n}+ sold in the last 24 hours",
        "{name} in {place} purchased this {n} minutes ago", "Popular choice! {n} travellers booked today",
        "{n} people added this to their cart today", "Bestseller in {place}, {n} sold this week"]),
    "confirmshaming": ("link", False, [
        "No thanks, I don't want to save money", "No, I'd rather pay full price", "I don't like good deals",
        "No, I prefer to miss out", "Skip, I'm fine with being unprotected", "No thanks, I hate saving",
        "I'll pass, I don't care about my {thing}", "No, I enjoy paying more", "Maybe later, I like taking risks",
        "No thanks, I don't want free {extra}", "I don't want to be smart with money", "No, I'm happy without {extra}"]),
    "preselected_addon": ("label", True, [
        "Add {extra} for {money}", "Yes, include {extra} ({money})", "Protect my order with {extra} for {money}",
        "Add {extra} to my booking, {money}", "Include {extra} for just {money}", "Yes, add {extra}",
        "Upgrade my order with {extra} {money}", "Add {extra} and get peace of mind for {money}",
        "I want {extra} ({money})", "Include {extra} {money}"]),
    "forced_action": ("text", False, [
        "Create an account to continue", "Sign in to see the final price", "You must install our app to proceed",
        "Register to unlock this deal", "Verify your phone number to continue booking", "Log in to view the total",
        "Accept notifications to proceed", "Enter your email to see the price", "Connect your account to continue",
        "Join our newsletter to complete checkout", "Download the app to finish your purchase"]),
    "subscription_trap": ("text", False, [
        "Your free trial will convert to a paid plan of {money} per month", "To cancel, call us between 9am and 11am",
        "Cancellations need {n} days' written notice", "Plan renews automatically until you cancel",
        "Contact support to end your membership", "You will be billed {money} every month after the trial",
        "Cancellation is only possible by phone", "A fee of {money} applies if you cancel early",
        "No refunds after the first {n} days", "Trial ends in {n} days, then you are charged {money}",
        "To stop renewal please write to our head office"]),
    "nagging": ("heading", False, [
        "Wait! Don't go yet", "Are you sure you want to leave?", "Before you go, get {pct}% off",
        "Turn on notifications to never miss a deal", "Rate us now", "You left something in your cart",
        "Don't miss out, complete your profile", "Allow location access for better deals",
        "Leaving so soon? Here's {pct}% off", "Enable push alerts", "One more thing before you leave"]),
    "none": ("text", False, [
        "Add to cart", "Continue to payment", "Convenience fee {money}", "Total {money}", "Order summary", "Sign in",
        "Search", "Choose your {thing}", "Departure {clock} from {place}", "Free cancellation until {n} hours before departure",
        "Terms and conditions", "Contact us", "Pay with UPI, card or net banking", "Delivery to {place} in {n} days",
        "Customer reviews", "{thing} available: {n}", "Optional: add {extra} for {money}", "Back to results",
        "Your booking is confirmed", "Prices include all taxes and fees", "No thanks", "Skip", "View details",
        "Sale runs until 31 December", "Selected dates: 12 Nov to 15 Nov", "Show more", "Filters", "Apply coupon",
        # site chrome, headings, form labels and ordinary names: the things a model must NOT flag
        "{event}", "{event} live in {place}", "{event} tickets", "Upcoming events", "Your cart", "Your basket",
        "Account settings", "My profile", "My bookings", "Billing details", "Billing history", "Payment method",
        "Card number", "Name on card", "Security code", "Expiry date", "Cancel membership", "Manage subscription",
        "Help centre", "Frequently asked questions", "Privacy policy", "Refund policy", "About us", "Careers",
        "Home", "Events", "Plans", "Pricing", "Log out", "Settings", "Notifications", "Orders", "Wishlist",
        "Select quantity", "Quantity: {n}", "Ticket x {n}", "{thing} x {n}", "Subtotal {money}", "GST {money}",
        "Choose a reason", "Update your card", "Download invoice", "Membership: Plus", "Plan: Standard",
        "Your membership has been cancelled", "Your order has been placed", "Thank you for your purchase",
        "Free shipping on orders above {money}", "Seats available: {n}", "Doors open at {clock}", "Venue: {place} arena"]),
}


def generate(n_per_class: int = 60, seed: int = 11) -> list[tuple[str, str, bool, str]]:
    r = random.Random(seed)
    rows: list[tuple[str, str, bool, str]] = []
    seen: set[tuple[str, str]] = set()
    for cls, (role, checked, templates) in TEMPLATES.items():
        target = n_per_class * (2 if cls == "none" else 1)
        tries = 0
        while sum(1 for k in seen if k[0] == cls) < target and tries < target * 20:
            tries += 1
            text = r.choice(templates).format(
                n=r.choice([1, 2, 3, 4, 5, 7, 9, 12, 17, 23, 37, 52, 128, 300]), pct=r.choice([40, 60, 75, 88, 94, 97, 15, 20, 30]),
                thing=r.choice(THINGS), place=r.choice(PLACES), name=r.choice(NAMES), verb=r.choice(VERBS),
                extra=r.choice(EXTRAS), money=_money(r), clock=_clock(r), unit=r.choice(UNITS), event=r.choice(EVENTS))
            if r.random() < 0.3:
                text = text.upper() if r.random() < 0.3 else text.lower() if r.random() < 0.5 else text + r.choice(["!", " >>", " ★", ""])
            if (cls, text.lower()) in seen:
                continue
            seen.add((cls, text.lower()))
            rows.append((text, r.choice([role, role, role, "button"]) if cls == "false_urgency" else role, checked, cls))
    return rows
