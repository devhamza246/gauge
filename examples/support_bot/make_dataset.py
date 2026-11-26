"""Regenerates dataset.jsonl (100 labelled tickets) deterministically: python make_dataset.py"""
import json
from pathlib import Path

TEMPLATES = {
    "billing": [
        ("I was charged twice for my invoice this month, please fix this ASAP", "high"),
        ("Where can I download last month's invoice?", "medium"),
        ("Question: can I pay by card instead of bank transfer?", "low"),
        ("My payment failed but the money left my card, I need a refund", "medium"),
        ("I'm wondering how the subscription price changes next year", "low"),
    ],
    "technical": [
        ("The API returns a 500 error on every request, we are down", "high"),
        ("The dashboard is slow when I open the reports page", "medium"),
        ("Just checking: is there a rate limit on the API?", "low"),
        ("The export button is broken and nothing downloads", "medium"),
        ("The app crash happens on startup and we cannot work", "high"),
    ],
    "account": [
        ("I am locked out of my account and cannot log in", "high"),
        ("How do I change my email address on the profile?", "low"),
        ("I forgot my password and the reset link has expired", "medium"),
        ("Is it possible to enable two-factor login for my team?", "low"),
        ("My username shows the old company name", "medium"),
    ],
    "shipping": [
        ("My package has not arrived and the tracking has not updated in a week", "medium"),
        ("The courier marked it delivered but nothing came, urgent please", "high"),
        ("Curious when the next batch will be shipped", "low"),
        ("The delivery address label has a typo", "medium"),
        ("Tracking says my package arrived at the wrong city", "medium"),
    ],
}
SUFFIXES = ["", " Thanks.", " (customer on the Pro plan)", " Order #{n}."]

rows = []
n = 1000
for round_ in range(5):
    for category, items in TEMPLATES.items():
        for text, priority in items:
            if len(rows) >= 100:
                break
            n += 1
            suffix = SUFFIXES[(len(rows) // 20 + round_) % len(SUFFIXES)].format(n=n)
            rows.append({"id": f"t{len(rows) + 1:03d}", "input": text + suffix,
                         "expected": {"category": category, "priority": priority},
                         "tags": [category]})
out = Path(__file__).with_name("dataset.jsonl")
out.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
print(f"wrote {len(rows)} cases to {out}")
