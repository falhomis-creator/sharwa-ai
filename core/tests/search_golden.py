"""P4 Task 16: the search golden set (semantic vs lexical) and its declared metric.

A small Arabic store catalog and labelled queries in four classes:

  exact    - the query uses the catalog's own words (lexical search must win these);
  morph    - the same words in another form: plural, no hamza, taa-marbuta as haa;
  synonym  - a different word for the same thing, incl. Gulf/Yemeni usage
             (جزمة = حذاء, شنطة = حقيبة, جوال = هاتف, برفان = عطر, لبس = ملابس);
  english  - the product named in English (customers mix scripts on WhatsApp).

THE METRIC (declared before any run, P4 plan "بمقياس معلن"):
  * Recall@3 per class - share of queries with an expected product in the top 3
    cards (the turn shows <= 3 products, repos_summary.MAX_SHOWN_PRODUCTS);
  * MRR@8 per class   - mean of 1/rank of the first expected product within the
    8 returned cards (0 when absent), as a tie-breaker view of ranking quality.

Pass bars for a REAL semantic provider (test_search_golden_db.py, live test):
  * no regression: exact-class Recall@3 stays 1.0 in hybrid mode, and no query
    that lexical search answers in its top 3 is lost by hybrid search;
  * lift: Recall@3 over the synonym + english classes >= LIVE_MIN_SEMANTIC_RECALL
    and strictly above the lexical baseline on the same queries.

ABSENT queries (F-P4-15) are scored separately: the share answered with no cards.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

from app.llm.port import EmbeddingResult, LlmUsage
from app.text.arabic import normalize

TOP_K = 3
LIVE_MIN_SEMANTIC_RECALL = 0.70

# platform_product_id -> (title, description, category)
CATALOG: dict[str, tuple[str, str, str]] = {
    "P-SHOE": ("حذاء رياضي جلد للرجال", "حذاء مريح للجري والمشي", "shoes"),
    "P-HEEL": ("حذاء كعب عالي نسائي", "حذاء سهرة أنيق", "shoes"),
    "P-SHIRT": ("قميص قطني رجالي أزرق", "قميص كم طويل", "men"),
    "P-DRESS": ("فستان سهرة طويل", "فستان نسائي للمناسبات", "women"),
    "P-ABAYA": ("عباية سوداء مطرزة", "عباية نسائية فضفاضة", "women"),
    "P-BAG": ("حقيبة يد جلدية", "حقيبة نسائية متوسطة الحجم", "bags"),
    "P-BACKPACK": ("حقيبة ظهر مدرسية", "حقيبة للطلاب بجيوب متعددة", "bags"),
    "P-PHONE": ("هاتف ذكي سامسونج", "هاتف بشاشة كبيرة وكاميرا", "electronics"),
    "P-WATCH": ("ساعة يد رجالية فضية", "ساعة مقاومة للماء", "accessories"),
    "P-PERFUME": ("عطر عود فاخر", "عطر شرقي ثابت", "beauty"),
    "P-SUNGLASS": ("نظارة شمسية", "نظارة بحماية من الأشعة", "accessories"),
    "P-KIDS": ("ملابس أطفال قطنية", "طقم ولادي للأطفال", "kids"),
    "P-SCARF": ("شال صوف نسائي", "شال شتوي دافئ", "women"),
}


@dataclass(frozen=True)
class GoldenQuery:
    qid: str
    cls: str
    query: str
    expected: frozenset[str]


def _q(qid: str, cls: str, query: str, *expected: str) -> GoldenQuery:
    return GoldenQuery(qid, cls, query, frozenset(expected))


QUERIES: tuple[GoldenQuery, ...] = (
    _q("E1", "exact", "حذاء رياضي", "P-SHOE"),
    _q("E2", "exact", "فستان سهرة", "P-DRESS"),
    _q("E3", "exact", "عطر عود", "P-PERFUME"),
    _q("E4", "exact", "ساعة يد رجالية", "P-WATCH"),
    _q("E5", "exact", "نظارة شمسية", "P-SUNGLASS"),
    _q("E6", "exact", "حقيبة ظهر", "P-BACKPACK"),
    _q("M1", "morph", "احذية رياضية", "P-SHOE"),
    _q("M2", "morph", "فساتين سهره", "P-DRESS"),
    _q("M3", "morph", "عبايه سودا", "P-ABAYA"),
    _q("M4", "morph", "حقائب ظهر", "P-BACKPACK"),
    _q("M5", "morph", "نظارات شمسيه", "P-SUNGLASS"),
    _q("S1", "synonym", "جزمة رياضية", "P-SHOE"),
    _q("S2", "synonym", "شنطة يد", "P-BAG"),
    _q("S3", "synonym", "جوال كاميرته ممتازة", "P-PHONE"),
    _q("S4", "synonym", "برفان", "P-PERFUME"),
    _q("S5", "synonym", "لبس اطفال", "P-KIDS"),
    _q("S6", "synonym", "شنطة مدرسة", "P-BACKPACK"),
    _q("N1", "english", "sport shoes", "P-SHOE"),
    _q("N2", "english", "perfume", "P-PERFUME"),
    _q("N3", "english", "backpack", "P-BACKPACK"),
    _q("N4", "english", "sunglasses", "P-SUNGLASS"),
)

CLASSES = ("exact", "morph", "synonym", "english")

# F-P4-15: products this store does NOT sell. The right answer is NO cards (the
# turn then hands off instead of showing unrelated products). Scored as the share
# of these queries that return an empty list.
ABSENT: tuple[str, ...] = ("قلم رصاص", "ثلاجة", "laptop computer")


def embed_text_for(pid: str) -> str:
    """The same text the batch worker embeds (title + ' ' + description)."""
    title, description, _cat = CATALOG[pid]
    return (title + " " + description).strip()


def rank_of(expected: frozenset[str], cards: list[str]) -> int | None:
    """1-based rank of the first expected product in the returned cards."""
    for i, pid in enumerate(cards, start=1):
        if pid in expected:
            return i
    return None


@dataclass(frozen=True)
class ClassScore:
    n: int
    recall_at_k: float
    mrr: float


def score(results: dict[str, list[str]], queries: tuple[GoldenQuery, ...] = QUERIES,
          k: int = TOP_K) -> dict[str, ClassScore]:
    """Recall@k and MRR (over the <= 8 returned cards) per class."""
    out: dict[str, ClassScore] = {}
    for cls in CLASSES:
        qs = [q for q in queries if q.cls == cls]
        ranks = [rank_of(q.expected, results[q.qid]) for q in qs]
        hits = sum(1 for r in ranks if r is not None and r <= k)
        mrr = sum(1.0 / r for r in ranks if r is not None) / len(qs)
        out[cls] = ClassScore(len(qs), hits / len(qs), round(mrr, 3))
    return out


def recall_over(results: dict[str, list[str]], classes: tuple[str, ...], k: int = TOP_K) -> float:
    qs = [q for q in QUERIES if q.cls in classes]
    hits = sum(1 for q in qs if (r := rank_of(q.expected, results[q.qid])) is not None and r <= k)
    return hits / len(qs)


def format_table(rows: dict[str, dict[str, ClassScore]]) -> str:
    """Plain-text report: one line per mode x class."""
    lines = [f"{'mode':<22}{'class':<9}{'n':>3}{'recall@3':>10}{'mrr@8':>8}"]
    for mode, scores in rows.items():
        for cls in CLASSES:
            s = scores[cls]
            lines.append(f"{mode:<22}{cls:<9}{s.n:>3}{s.recall_at_k:>10.2f}{s.mrr:>8.3f}")
    return "\n".join(lines)


# --- the offline semantic ORACLE -------------------------------------------------
# NOT a quality claim: a deterministic stand-in that behaves the way a semantic
# model is supposed to (synonyms, plurals and English names land on one concept),
# so the offline test can prove the PLUMBING - model-scoped vectors + RRF fusion
# lift recall when vectors are semantic, without breaking lexical hits. Whether a
# real model behaves like this is exactly what the live test measures.

_CONCEPTS: dict[str, tuple[str, ...]] = {
    "shoe": ("حذاء", "احذية", "جزمة", "جزم", "shoes", "shoe"),
    "sport": ("رياضي", "رياضية", "sport"),
    "dress": ("فستان", "فساتين"),
    "evening": ("سهرة",),
    "abaya": ("عباية", "عبايات"),
    "black": ("سوداء", "سودا"),
    "bag": ("حقيبة", "حقائب", "شنطة", "شنط"),
    "back": ("ظهر", "مدرسية", "مدرسة", "backpack"),
    "hand": ("يد",),
    "phone": ("هاتف", "جوال", "موبايل"),
    "camera": ("كاميرا", "كاميرته"),
    "perfume": ("عطر", "برفان", "perfume"),
    "watch": ("ساعة",),
    "glasses": ("نظارة", "نظارات", "sunglasses"),
    "sun": ("شمسية", "شمسيه"),
    "clothes": ("ملابس", "لبس"),
    "kids": ("اطفال", "أطفال", "ولادي"),
}
_TOKEN_TO_CONCEPT = {normalize(tok): c for c, toks in _CONCEPTS.items() for tok in toks}
# "backpack"/"sunglasses" name two concepts at once.
_MULTI = {"backpack": ("bag", "back"), "sunglasses": ("glasses", "sun")}
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _oracle_vector(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for tok in _TOKEN_RE.findall(normalize(text)):
        keys = _MULTI.get(tok) or ((_TOKEN_TO_CONCEPT[tok],) if tok in _TOKEN_TO_CONCEPT else ("w:" + tok,))
        for key in keys:
            idx = int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % dim  # noqa: S324 - bucket hash
            vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


class OracleSemanticProvider:
    model_name = "golden-oracle"

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    def embed(self, *, texts: list[str], timeout_s: float) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=[_oracle_vector(t, self._dim) for t in texts],
            usage=LlmUsage(0, 0, self.model_name, "oracle", 1),
        )
