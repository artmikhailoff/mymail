"""Topic classification for mailbox census.

Uses Gmail category labels, sender domains, and subject patterns.
Headers only — no body download required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Literal

Topic = Literal[
    "noise",
    "health",
    "construction",
    "finance",
    "travel",
    "school",
    "shopping",
    "accounts",
    "personal",
    "other",
]

TOPICS: tuple[Topic, ...] = (
    "noise",
    "health",
    "construction",
    "finance",
    "travel",
    "school",
    "shopping",
    "accounts",
    "personal",
    "other",
)


@dataclass(frozen=True)
class Classification:
    topic: Topic
    reasons: tuple[str, ...]
    confidence: Literal["high", "medium", "low"]

    def __str__(self) -> str:
        why = "; ".join(self.reasons) if self.reasons else ""
        return f"{self.topic}" + (f" ({why})" if why else "")


def sender_domain(from_addr: str) -> str:
    text = (from_addr or "").strip().lower()
    match = re.search(r"<([^>]+)>", text)
    addr = match.group(1) if match else text
    if "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[-1].strip().rstrip(">").strip()


def sender_local(from_addr: str) -> str:
    text = (from_addr or "").strip().lower()
    match = re.search(r"<([^>]+)>", text)
    addr = match.group(1) if match else text
    if "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[0].strip()


def _domain_matches(domain: str, suffixes: Iterable[str]) -> str | None:
    if not domain:
        return None
    for suffix in suffixes:
        if domain == suffix or domain.endswith("." + suffix):
            return suffix
    return None


def _label_set(labels: Iterable[str] | None) -> set[str]:
    return {str(item).lower() for item in (labels or [])}


# --- domain lists -----------------------------------------------------------

HEALTH_DOMAINS: tuple[str, ...] = (
    "invitro.ru",
    "gemotest.ru",
    "helix.ru",
    "kdl.ru",
    "kdltest.ru",
    "cmd-online.ru",
    "citilab.ru",
    "dnkom.ru",
    "medsi.ru",
    "emias.mos.ru",
    "smclinic.ru",
    "smpost.ru",
    "stomed.ru",
    "fomin-clinic.ru",
    "gmsclinic.ru",
    "emcmos.ru",
    "medswiss.ru",
    "euromed.ru",
    "questdiagnostics.com",
    "questhealth.com",
    "labcorp.com",
    "sonoraquest.com",
    "mychart.com",
    "epic.com",
    "kp.org",
    "kaiserpermanente.org",
    "athenahealth.com",
    "healow.com",
    "followmyhealth.com",
    "onemedical.com",
    "optum.com",
    "optumrx.com",
    "uhc.com",
    "anthem.com",
    "cigna.com",
    "aetna.com",
    "medicare.gov",
    "hioscar.com",
    "express-scripts.com",
)

CONSTRUCTION_DOMAINS: tuple[str, ...] = (
    "homedepot.com",
    "order.homedepot.com",
    "lowes.com",
    "menards.com",
    "build.com",
    "ferguson.com",
    "grainger.com",
    "hdsupply.com",
    "uline.com",
    "flooranddecor.com",
    "ikea.com",
    "blinds.com",
    "sherwin-williams.com",
    "benjaminmoore.com",
    "andersonwindows.com",
    "thermatru.com",
    "jeld-wen.com",
    "masonite.com",
    "pella.com",
    "marvin.com",
    "schlage.com",
    "kwikset.com",
    "ring.com",
    "nest.com",
    "angi.com",
    "thumbtack.com",
    "houzz.com",
    "porch.com",
    "homeadvisor.com",
    "taskrabbit.com",
    "radonaway.com",
    "leroymerlin.ru",
    "obi.ru",
    "petrovich.ru",
    "vseinstrumenti.ru",
    "sdvor.com",
)

FINANCE_DOMAINS: tuple[str, ...] = (
    "chase.com",
    "bankofamerica.com",
    "wellsfargo.com",
    "capitalone.com",
    "americanexpress.com",
    "discover.com",
    "citibank.com",
    "citi.com",
    "ally.com",
    "schwab.com",
    "fidelity.com",
    "vanguard.com",
    "paypal.com",
    "venmo.com",
    "stripe.com",
    "intuit.com",
    "turbotax.com",
    "hrblock.com",
    "irs.gov",
    "sberbank.ru",
    "tinkoff.ru",
    "tbank.ru",
    "alfabank.ru",
    "vtb.ru",
    "raiffeisen.ru",
    "qiwi.com",
    "yoomoney.ru",
)

TRAVEL_DOMAINS: tuple[str, ...] = (
    "aviasales.ru",
    "tutu.ru",
    "city.travel",
    "booking.com",
    "airbnb.com",
    "expedia.com",
    "hotels.com",
    "kayak.com",
    "tripadvisor.com",
    "united.com",
    "delta.com",
    "aa.com",
    "jetblue.com",
    "southwest.com",
    "ryanair.com",
    "aeroflot.ru",
    "s7.ru",
    "pobeda.aero",
    "rzd.ru",
    "uber.com",
    "lyft.com",
    "marriott.com",
    "hilton.com",
    "ihg.com",
    "hyatt.com",
)

SCHOOL_DOMAINS: tuple[str, ...] = (
    "parentsquare.com",
    "fairfieldschools.org",
    "powerschool.com",
    "schoology.com",
    "canvaslms.com",
    "clever.com",
    "edmodo.com",
    "classdojo.com",
    "remind.com",
)

SHOPPING_DOMAINS: tuple[str, ...] = (
    "amazon.com",
    "amazon.co.uk",
    "amazon.de",
    "ebay.com",
    "etsy.com",
    "walmart.com",
    "target.com",
    "bestbuy.com",
    "costco.com",
    "apple.com",
    "store.apple.com",
    "email.apple.com",
    "newegg.com",
    "aliexpress.com",
    "wildberries.ru",
    "ozon.ru",
    "avito.ru",
    "market.yandex.ru",
    "dns-shop.ru",
    "mvideo.ru",
    "citilink.ru",
    "wayfair.com",
    "harborfreight.com",
)

ACCOUNTS_DOMAINS: tuple[str, ...] = (
    "openai.com",
    "email.openai.com",
    "google.com",
    "accounts.google.com",
    "github.com",
    "microsoft.com",
    "accountprotection.microsoft.com",
    "appleid.apple.com",
    "id.apple.com",
    "twitter.com",
    "x.com",
    "dropbox.com",
    "slack.com",
    "zoom.us",
    "notion.so",
    "atlassian.com",
    "noreply.github.com",
)

NOISE_DOMAINS: tuple[str, ...] = (
    "mailchimp.com",
    "sendgrid.net",
    "constantcontact.com",
    "klaviyo.com",
    "hubspot.com",
    "marketo.com",
    "salesforce.com",
    "medium.com",
    "substack.com",
    "beehiiv.com",
    "linkedin.com",
    "facebookmail.com",
    "pinterest.com",
    "instagram.com",
    "tiktok.com",
    "spotify.com",
    "netflix.com",
    "disneyplus.com",
    "hulu.com",
    "nytimes.com",
    "washingtonpost.com",
    "wsj.com",
    "forbes.com",
    "retailmenot.com",
    "groupon.com",
    "michaels.com",
    "emdeals.michaels.com",
    "nerdwallet.com",
    "nextdoor.com",
    "email.nextdoor.com",
    "duolingo.com",
    "shein.com",
    "emailmarket.shein.com",
    "gl-inet.com",
    "newsletter.gl-inet.com",
    "timberland.com",
    "t.timberland.com",
    "z1motorsports.com",
    "wholefoodsmarket.com",
    "mail.wholefoodsmarket.com",
    "cp.ru",
)

# Free-mail hosts often mean a person wrote the message.
PERSONAL_HOSTS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.uk",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "proton.me",
        "protonmail.com",
        "mail.ru",
        "bk.ru",
        "inbox.ru",
        "list.ru",
        "yandex.ru",
        "ya.ru",
        "rambler.ru",
    }
)

NOREPLY_LOCAL = re.compile(
    r"^(no[-_]?reply|do[-_]?not[-_]?reply|noreply|donotreply|mailer[-_]?daemon|"
    r"notifications?|alert|alerts|news|newsletter|marketing|promo|support|"
    r"info|hello|team|newsroom|digest|updates?)$",
    re.IGNORECASE,
)

_HEALTH_SUBJECT = re.compile(
    r"("
    r"результат\w*\s+(исслед|анализ)|"
    r"результаты\s+(анализ|готов)|"
    r"лаборатор|"
    r"invitro|инвитро|гемотест|"
    r"lab\s+result|test\s+result|blood\s+(test|work)|"
    r"pathology|imaging\s+result|mychart|"
    r"медицинск\w*\s+заключен|"
    r"диспансеризац|"
    r"prescription|аптек"
    r")",
    re.IGNORECASE,
)

_CONSTRUCTION_SUBJECT = re.compile(
    r"("
    r"home\s*depot|lowe'?s|"
    r"\b(door|doors|window|windows|flooring|cabinets?|countertops?)\b|"
    r"\b(contractor|permit|renovation|remodel|hvac|plumbing|electrical)\b|"
    r"\b(drywall|lumber|insulation|roofing|siding|fence|deck)\b|"
    r"ремонт|строител|двери|окна|плитка|ламинат|кухн|"
    r"сантехник|электрик|отделк|капремонт|смет"
    r")",
    re.IGNORECASE,
)

_FINANCE_SUBJECT = re.compile(
    r"("
    r"statement|balance|payment\s+due|wire\s+transfer|"
    r"tax\s+(return|refund|document)|1099|w-?2|"
    r"invoice|receipt|billing|"
    r"выписк|счет\s+на\s+оплату|перевод|налог|"
    r"кредитн\w*\s+карт|дебет"
    r")",
    re.IGNORECASE,
)

_TRAVEL_SUBJECT = re.compile(
    r"("
    r"boarding\s+pass|itinerary|flight\s+(confirm|status)|"
    r"hotel\s+(confirm|reservation)|check[- ]?in|"
    r"ваш\s+билет|билеты\s+по\s+вашему|бронирован|"
    r"авиабилет|поезд"
    r")",
    re.IGNORECASE,
)

_SCHOOL_SUBJECT = re.compile(
    r"("
    r"parentsquare|district\s+news|school\s+(newsletter|announcement)|"
    r"report\s+card|attendance|parent[- ]teacher|"
    r"школ|классн\w*\s+руковод|дневник"
    r")",
    re.IGNORECASE,
)

_SHOPPING_SUBJECT = re.compile(
    r"("
    r"your\s+order|order\s+(shipped|delivered|confirmed)|"
    r"tracking\s+number|out\s+for\s+delivery|"
    r"заказ\s+(отправлен|доставлен|оформлен)|трек[- ]?номер|"
    r"доставк"
    r")",
    re.IGNORECASE,
)

_ACCOUNTS_SUBJECT = re.compile(
    r"("
    r"verification\s+code|security\s+code|authentication\s+code|"
    r"one[- ]time\s+(passcode|password)|otp\b|"
    r"sign[- ]?in|new\s+sign[- ]?in|password\s+reset|"
    r"keeping\s+your\s+.*\s+account\s+secure|"
    r"код\s+подтвержден|код\s+безопасност|двухфактор"
    r")",
    re.IGNORECASE,
)

# "Your Savings Bank Statement" / "Rewards Card Statement" are not promos.
_STATEMENT_SUBJECT = re.compile(r"\b(statement|receipt|invoice|autopay)\b", re.IGNORECASE)

_REPLY_SUBJECT = re.compile(r"^\s*(re|fw|fwd|aw|ответ|отв)\s*:", re.IGNORECASE)

_TRANSACTIONAL_SUBJECT = re.compile(
    r"("
    r"\b(receipts?|invoice|statement|refund|autopay|auto-pay|shipped|delivered|tracking|dismissal)\b|"
    r"\byour\s+(\w+\s+){0,3}(order|orders|trip|ride|booking|reservation|payment|bill|"
    r"shipping|subscription|policy|appointment|listing|claim)\b|"
    r"\bstay at\b|\bappointment\b|"
    r"\border\s+(#|number|confirm)|\bpayment\b|"
    r"чек|квитанц|автоплат|сч[её]т|заказ"
    r")",
    re.IGNORECASE,
)

_NOISE_SUBJECT = re.compile(
    r"("
    r"%\s*off|up\s+to\s+\$|sale\s+ends|flash\s+sale|limited\s+time|"
    r"daily\s+deals|clearance|best\s+deals|weekly\s+deals|"
    r"\bsavings\b|\brewards?\b|you\s+could\s+(win|be)|"
    r"unsubscribe|newsletter|weekly\s+digest|deal\s+of|"
    r"скидк|акци[яи]|распродаж|подписк"
    r")",
    re.IGNORECASE,
)

# Host fragments that almost always mean marketing, even under a retailer domain.
_MARKETING_HOST_FRAGMENTS: tuple[str, ...] = (
    "mg.homedepot.",
    "members.wayfair.",
    "emailmarket.",
    "news.market.",
    "emdeals.",
    "ecomm.",
    "newsletter.",
    "discover.",
    "explore.",
    "em1.",
    "mail.nerdwallet.",
)

# Retail / bank marketing that should not count as real topic mail.
_PROMO_TOPICS = frozenset({"construction", "shopping", "finance", "travel"})
_PROMO_LOCAL_TOKENS = (
    "news",
    "market",
    "promo",
    "deal",
    "editor",
    "customercare",
    "customer",
    "decor",
    "rewards",
    "hello",
    "info",
)


def classify_message(
    *,
    from_addr: str,
    subject: str,
    labels: list[str] | None = None,
) -> Classification:
    """Return a topic label for one message from header fields."""
    domain = sender_domain(from_addr)
    local = sender_local(from_addr)
    subject_text = subject or ""
    labels_l = _label_set(labels)
    reasons: list[str] = []
    # Weak noise rules (sender local-part only) must not junk these.
    protected = (
        bool(_REPLY_SUBJECT.match(subject_text))
        or "\\important" in labels_l
        or bool(_TRANSACTIONAL_SUBJECT.search(subject_text))
    )

    # Gmail categories are a strong noise signal.
    if any("category_promotions" in lab or lab.endswith("promotions") for lab in labels_l):
        return Classification("noise", ("gmail promotions",), "high")
    if any("category_social" in lab or lab.endswith("social") for lab in labels_l):
        return Classification("noise", ("gmail social",), "high")

    # Promo subject wins over retailer/bank domains (ads ≠ real topic mail),
    # but schools and doctors don't advertise ("Grade 2 Newsletter" is school).
    if _NOISE_SUBJECT.search(subject_text) and not (
        _domain_matches(domain, SCHOOL_DOMAINS)
        or _domain_matches(domain, HEALTH_DOMAINS)
        or _STATEMENT_SUBJECT.search(subject_text)
    ):
        return Classification("noise", ("noise subject",), "medium")

    if any(fragment in domain for fragment in _MARKETING_HOST_FRAGMENTS):
        return Classification("noise", (f"marketing host {domain}",), "high")

    # Domain allow-lists (topic-specific first, then noise).
    for topic, domains, tag in (
        ("health", HEALTH_DOMAINS, "health sender"),
        ("construction", CONSTRUCTION_DOMAINS, "construction sender"),
        ("finance", FINANCE_DOMAINS, "finance sender"),
        ("travel", TRAVEL_DOMAINS, "travel sender"),
        ("school", SCHOOL_DOMAINS, "school sender"),
        ("shopping", SHOPPING_DOMAINS, "shopping sender"),
        ("accounts", ACCOUNTS_DOMAINS, "accounts sender"),
        ("noise", NOISE_DOMAINS, "noise sender"),
    ):
        hit = _domain_matches(domain, domains)
        if hit:
            # Marketing@ / newsletter hosts under a topic domain → still noise.
            if topic in _PROMO_TOPICS and not protected and (
                NOREPLY_LOCAL.match(local)
                or any(tok in local for tok in _PROMO_LOCAL_TOKENS)
            ):
                return Classification(
                    "noise",
                    (f"promo local-part on {hit}",),
                    "medium",
                )
            return Classification(topic, (f"{tag} {hit}",), "high")  # type: ignore[arg-type]

    # Subject patterns (noise already handled above).
    for topic, pattern, tag in (
        ("health", _HEALTH_SUBJECT, "health subject"),
        ("construction", _CONSTRUCTION_SUBJECT, "construction subject"),
        ("finance", _FINANCE_SUBJECT, "finance subject"),
        ("travel", _TRAVEL_SUBJECT, "travel subject"),
        ("school", _SCHOOL_SUBJECT, "school subject"),
        ("shopping", _SHOPPING_SUBJECT, "shopping subject"),
        ("accounts", _ACCOUNTS_SUBJECT, "accounts subject"),
    ):
        if pattern.search(subject_text):
            return Classification(topic, (tag,), "medium")  # type: ignore[arg-type]

    # Updates category without a topic match → often transactional noise-adjacent.
    if any("category_updates" in lab or lab.endswith("updates") for lab in labels_l):
        if NOREPLY_LOCAL.match(local) and not protected:
            return Classification("noise", ("gmail updates + noreply",), "medium")
        return Classification("other", ("gmail updates",), "low")

    # Personal heuristic: free-mail host, not noreply, not bulk-looking.
    if domain in PERSONAL_HOSTS and not NOREPLY_LOCAL.match(local):
        if not any(
            token in local
            for token in ("news", "noreply", "mailer", "notify", "newsletter", "promo")
        ):
            reasons.append(f"personal host {domain}")
            return Classification("personal", tuple(reasons), "medium")

    if NOREPLY_LOCAL.match(local) or "noreply" in local or "no-reply" in local:
        if protected:
            return Classification("other", ("automated, but reply/important/transactional",), "low")
        return Classification("noise", ("noreply local-part",), "low")

    return Classification("other", ("no strong rule",), "low")


def classify_record(record: dict) -> Classification:
    return classify_message(
        from_addr=str(record.get("from") or ""),
        subject=str(record.get("subject") or ""),
        labels=list(record.get("labels") or []),
    )
