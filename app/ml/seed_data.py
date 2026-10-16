"""Hand-written data for the dark-pattern classifier.

SEED    : tiny offline fallback so the model trains even without the generated corpus.
HELDOUT : a human-written test set, never used for training. It mixes obvious cases with
          hard negatives (honest deadlines, neutral 'No thanks', plain fee rows).

Each row is (text, role, near_checked_checkbox).
"""
from __future__ import annotations

Row = tuple[str, str, bool]

SEED: dict[str, list[Row]] = {
    "none": [
        ("Add to cart", "button", False), ("Sign in", "link", False), ("Search flights", "button", False),
        ("Terms and conditions", "link", False), ("Order summary", "heading", False),
        ("Free cancellation until 24 hours before departure", "text", False),
    ],
    "false_urgency": [
        ("Deal ends in 04:59", "text", False), ("Limited time offer - book within 10 minutes", "text", False),
        ("Price will go up at midnight!", "text", False), ("Hurry, this offer expires today", "text", False),
        ("Your reservation is held for 05:00 minutes", "text", False), ("Flash sale ends soon", "heading", False),
    ],
    "scarcity": [
        ("Only 1 room left at this price", "text", False), ("Almost sold out!", "text", False),
        ("Just 3 seats remaining", "text", False), ("Low stock - order soon", "text", False),
        ("Last ticket at this price", "text", False), ("94% of tickets already sold", "text", False),
    ],
    "fake_social_proof": [
        ("52 people are looking at this hotel right now", "text", False), ("Aman from Pune just booked this", "text", False),
        ("300 bought in the last 24 hours", "text", False), ("Trending now - everyone is booking this", "text", False),
        ("17 other travellers are viewing this flight", "text", False), ("Booked 2,400 times this week", "text", False),
    ],
    "confirmshaming": [
        ("No, I prefer paying full price", "link", False), ("No thanks, I don't want to save money", "link", False),
        ("I'll skip it, I like taking risks", "link", False), ("No, I don't need a great deal", "button", False),
        ("Maybe later, I don't care about my health", "link", False), ("No thanks, I'm happy to miss out", "link", False),
    ],
    "preselected_addon": [
        ("Add travel insurance for Rs 199", "label", True), ("Yes, add priority boarding", "label", True),
        ("Include premium support for $4.99", "label", True), ("Donate Rs 10 to charity", "label", True),
        ("Add extra legroom seat", "label", True), ("Protect my order with Buyer Shield", "label", True),
    ],
    "forced_action": [
        ("Create an account to see the final price", "text", False), ("You must install the app to continue", "text", False),
        ("Sign in to complete your booking", "text", False), ("Agree to receive marketing emails to proceed", "label", False),
        ("Share your contacts to continue", "text", False), ("Log in with phone number to view the offer", "text", False),
    ],
    "subscription_trap": [
        ("Your free trial converts to a paid plan automatically", "text", False),
        ("To cancel, call our support line between 9 and 11 am", "text", False),
        ("Cancellation requires 30 days written notice", "text", False),
        ("Contact customer care to end your membership", "text", False),
        ("Membership renews every month until you cancel", "text", False),
    ],
    "nagging": [
        ("Wait! Before you go, get 20% off", "heading", False), ("Are you sure you want to leave?", "heading", False),
        ("Turn on notifications to never miss a deal", "text", False), ("Rate this app now", "text", False),
        ("Don't leave yet! Your cart misses you", "text", False), ("Enable location for better offers", "text", False),
    ],
}

HELDOUT: dict[str, list[Row]] = {
    "none": [
        ("Continue to payment", "button", False), ("Convenience fee Rs 80", "text", False),
        ("Total Rs 1,277", "text", False), ("Sale runs until 31 December. Prices shown include all taxes", "text", False),
        ("No thanks", "button", False), ("Departure 06:45 arrival 08:55", "text", False),
        ("Seats available: 120", "text", False), ("Pay securely with UPI, cards or net banking", "text", False),
        ("Optional: add baggage for Rs 600", "text", False), ("Your booking confirmation will be emailed to you", "text", False),
        ("Choose your seat", "heading", False), ("About us", "link", False), ("Customer reviews (4.2 out of 5)", "heading", False),
        ("Free cancellation within 24 hours of booking", "text", False), ("Update card", "link", False),
    ],
    "false_urgency": [
        ("Offer valid for the next 09:58 only", "text", False), ("Book now - price jumps in 2 hours", "text", False),
        ("Today only: 40% off ends at midnight", "text", False), ("Complete your booking in 04:30 or lose this fare", "text", False),
        ("Hurry! Sale closing in a few minutes", "heading", False), ("Countdown: deal expires in 00:12:44", "text", False),
        ("Prices rise after this timer hits zero", "text", False), ("Act fast, this rate won't last", "text", False),
    ],
    "scarcity": [
        ("Only 2 left in stock", "text", False), ("Hot! Just 1 seat left on this flight", "text", False),
        ("We have only 4 rooms left for your dates", "text", False), ("Selling fast - 88% booked", "text", False),
        ("Limited availability, 3 tickets remaining at this price", "text", False), ("Last pair in your size", "text", False),
        ("Few items left", "text", False), ("Almost gone: only 5 units available", "text", False),
    ],
    "fake_social_proof": [
        ("63 people are viewing this right now", "text", False), ("Riya from Delhi booked this 3 minutes ago", "text", False),
        ("1,284 customers purchased this today", "text", False), ("Everyone's booking this - trending in your city", "text", False),
        ("29 others are looking at this room", "text", False), ("Bestseller! Sold 500+ in the last hour", "text", False),
        ("Someone in Mumbai just bought the last one", "text", False), ("97 people added this to cart today", "text", False),
    ],
    "confirmshaming": [
        ("No thanks, I'd rather pay more", "link", False), ("I don't want free delivery", "link", False),
        ("No, I'm not interested in saving money", "button", False), ("Skip - I enjoy missing out on rewards", "link", False),
        ("No, I'll stay unprotected", "link", False), ("I don't care about my family's safety", "link", False),
        ("Nope, I like overpaying", "link", False), ("Not now, I don't want a better deal", "link", False),
    ],
    "preselected_addon": [
        ("Add trip protection for $12.99", "label", True), ("Yes, include express handling", "label", True),
        ("Add SMS alerts for Rs 49", "label", True), ("Include gift wrap for Rs 99", "label", True),
        ("Add extended warranty - 2 years", "label", True), ("Donate Rs 5 to our partner NGO", "label", True),
        ("Insure my parcel for Rs 79", "label", True), ("Add Plus membership trial to my order", "label", True),
    ],
    "forced_action": [
        ("Please register to view prices", "text", False), ("Download our app to complete this purchase", "text", False),
        ("You need an account to check out", "text", False), ("Accept notifications to continue booking", "label", False),
        ("Verify your phone number to see this deal", "text", False), ("Connect your social account to proceed", "text", False),
        ("Enter your email to unlock the price", "text", False), ("Subscribe to our newsletter to continue", "label", False),
    ],
    "subscription_trap": [
        ("Plan auto-renews at Rs 499 per month unless cancelled", "text", False),
        ("Cancellations are only accepted by phone on weekdays", "text", False),
        ("Your trial ends in 3 days and you will be charged", "text", False),
        ("To stop billing please write to our head office", "text", False),
        ("Cancellation fee of 3 months applies", "text", False),
        ("Pausing is easy. Cancelling requires a call to a specialist", "text", False),
        ("You will be billed annually, no refunds after the first week", "text", False),
    ],
    "nagging": [
        ("Wait, don't go yet!", "heading", False), ("Leaving so soon? Here's 15% off", "heading", False),
        ("Allow notifications for exclusive offers", "text", False), ("We'd love your feedback - rate us!", "text", False),
        ("You left something in your cart", "text", False), ("Before you leave, join our newsletter", "text", False),
        ("Please enable location access", "text", False), ("Don't miss out! Update your profile now", "text", False),
    ],
}
