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
