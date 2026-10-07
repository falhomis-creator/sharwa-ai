"""core/app/workers/templates.py - approved outbound templates (H23).

Every text sent to a customer in this batch is a fixed template in this ONE
module - no free-form composition, no field taken from any customer message
(H19/H23). The three texts below are the OWNER-APPROVED verbatim strings
(executive directive, overriding the suggested defaults in PROMPT_P1_02 §6.5):

  handoff_notice - human handoff notice
  optout_confirm - opt-out confirmation (order_updates keeps flowing, exactly
                   as the suppression logic does NOT block service)
  safe_ack       - safe acknowledgement when ai_reply kill-switch is off

Constraint honored (OQ-P1-08): no promise of a specific time ("في أقرب وقت"
not "خلال 5 دقائق"); no word "بوت"/"ذكاء اصطناعي"/technical term.
"""
from __future__ import annotations

# Owner-approved, verbatim (do not edit without a new approval).
TEMPLATES: dict[str, str] = {
    "handoff_notice": (
        "أهلاً بك! لضمان خدمتك بأفضل شكل، قمت بتحويل محادثتك لأحد ممثلي خدمة "
        "العملاء وسيكون معك في أقرب وقت. 🕒"
    ),
    "optout_confirm": (
        "تم إيقاف الرسائل الترويجية والتسويقية بنجاح. سنستمر فقط في إرسال "
        "التحديثات الهامة الخاصة بحالة طلباتك لضمان وصول شحناتك في الوقت المحدد. 📦"
    ),
    # P3.3 opt-in confirmation. PROPOSED text (OQ-P3-10) kept VERBATIM until
    # the owner decides otherwise - it is a reply to an inbound message, not a
    # proactive send, so it is NOT in POLICY_EXEMPT_TEMPLATES either.
    "optin_confirm": (
        "تم تفعيل الرسائل الترويجية بنجاح. يمكنك إيقافها في أي وقت بإرسال كلمة «إيقاف». 🎁"
    ),
    "safe_ack": "وصلتنا رسالتك، شكراً لتواصلك معنا! سيتم مراجعتها قريباً.",
    # P1.7 order tracking (PROMPT §6). Conservative, no time promise, no
    # "بوت"/"ذكاء اصطناعي" (OQ-P1-08). order_unverified + order_blocked hand off.
    "order_need_ref": "أرسل لي رقم طلبك لأتحقق من حالته من فضلك. 📋",
    "order_need_phone": "لأسباب أمنية، أرسل لي رقم الهاتف المسجّل على هذا الطلب لأتمكن من التحقق منه. 🔒",
    "order_unverified": "لم أتمكن من التحقق من هذا الطلب. سأحوّل محادثتك لأحد ممثلي خدمة العملاء لمساعدتك مباشرة.",
    "order_blocked": "لأسباب أمنية، سأحوّل محادثتك لأحد ممثلي خدمة العملاء لمساعدتك في طلبك.",
    "order_status_unknown": "وصلتني حالة طلبك لكنها غير متاحة حالياً. سأحوّلك لأحد ممثلي خدمة العملاء.",
    "order_unavailable": "خدمة تتبع الطلبات غير متاحة حالياً. سأحوّل محادثتك لأحد ممثلي خدمة العملاء.",
    # P2.2 address resolver (PROMPT §7). Conservative, no time promise, no
    # "بوت"/"ذكاء اصطناعي" (OQ-P1-08), and NO coordinates in text (H67).
    "address_need_pin": "لأحدد عنوان التوصيل بدقة، أرسل لي موقعك (الدبوس) من فضلك. 📍",
    "address_confirm": "هل تقصد «name»؟",
    "address_disambiguate": "وجدت أكثر من مكان مطابق:\n«options»\nأيها تقصد؟",
    "address_rejected": "عذراً، لم أتمكن من تحديد عنوان التوصيل. سأحوّل محادثتك لأحد ممثلي خدمة العملاء.",
    "address_out_of_coverage": "الموقع الذي أرسلته خارج منطقة التغطية حالياً. أرسل موقعاً آخر من فضلك.",
    # P2.3 back-in-stock (PROMPT §5.4). Conservative; H35: title only («name»),
    # never a price and never an available quantity («توفّر» not «بقيت 3 قطع»).
    "stock_joined": "تم تسجيلك في قائمة الانتظار لهذا المنتج. سنُشعرك فور توفره. 📦",
    "stock_available": "عاد «name» للتوفر! سارع بالطلب الآن. 🛍️",
    "stock_hold_expired": "انتهت مدة حجزك. ما زال بإمكانك الطلب من جديد متى شئت.",
    "stock_cancelled": "تم إلغاء تسجيلك من قائمة الانتظار.",
    "stock_already_waiting": "أنت مسجّل مسبقاً في قائمة الانتظار لهذا المنتج. سنُشعرك فور توفره.",
    "stock_unavailable": "هذا المنتج غير متوفر حالياً. هل تريد أن أسجّلك في قائمة الانتظار ليصل لك تنبيه فور توفره؟",
    # P4 Task 18b-1 size advice (SizeAdvisor §5.1). PROPOSED texts (OQ-P4-21) kept
    # VERBATIM until the owner approves or edits them. Contract (OQ-P4-15/16): a
    # size label is ALWAYS written right after the word «مقاس» (so the Verifier
    # captures it) and filled from the advice only (compose_size_reply); no
    # size-ish words (صغير/وسط/كبير/small/medium/large) anywhere; one line each.
    "size_recommend": "المقاس المناسب لك: مقاس «size» ✅",
    "size_recommend_alt": "المقاس المناسب لك: مقاس «size»، ويمكنك أيضاً تجربة مقاس «alt». ✅",
    "size_nearest_out_of_range": (
        "أقرب مقاس لك في جدول هذا المنتج هو مقاس «size»، لكن قياساتك خارج نطاق الجدول، "
        "لذا قد لا يكون مناسباً تماماً."
    ),
    "size_need_inputs": "لأقترح عليك المقاس المناسب، أرسل لي طولك بالسنتيمتر ووزنك بالكيلو من فضلك. 📏",
    "size_no_chart": (
        "لا يتوفر جدول مقاسات لهذا المنتج حالياً. سأحوّل محادثتك لأحد ممثلي خدمة العملاء لمساعدتك."
    ),
    "size_no_fit": (
        "لا يوجد في جدول هذا المنتج مقاس يناسب قياساتك. سأحوّل محادثتك لأحد ممثلي خدمة العملاء لمساعدتك."
    ),
}


def template_text(template_id: str) -> str:
    """Return the approved text for a template id (KeyError if unknown - a
    programming bug, never a fallback to some invented string)."""
    return TEMPLATES[template_id]


# D1 / F-P1-06: safe_ack is the ONLY template exempt from the ai_reply gate. It
# is the literal "we got your message" that §4.10 requires when the kill-switch
# is OFF (off = "route to staff + 'we got your message'") - so it must reach the
# customer even though ai_reply is off. The dispatcher skips the ai_reply gate
# for it. This is a CLOSED list: adding anything to it is a policy decision
# (PROMPT_P1_03 §0, F-P1-06), never a programming decision.
POLICY_EXEMPT_TEMPLATES = frozenset({"safe_ack"})

# P1.6 (§6): the templates the output verifier may send as its SAFE fallback when
# a composed reply violates a blocklist. This is a CLOSED list - expanding it is a
# policy decision (like POLICY_EXEMPT_TEMPLATES), never a programming decision.
SAFE_FALLBACK_TEMPLATES = frozenset({"handoff_notice", "safe_ack"})
