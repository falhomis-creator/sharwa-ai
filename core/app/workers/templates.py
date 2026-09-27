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
