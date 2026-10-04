"""Query -> keywords. Shared by the FTS, entity and graph arms.

Natural sentences ("hey, what theme do I like in Obsidian?") must not be used
as a single AND-query or a single LIKE needle, so we reduce them to content
words first.
"""
from __future__ import annotations

import re
import unicodedata

_STOPWORDS = frozenset(
    """
    a about above after again all also am an and any are as at be because been before being below
    between both but by can could did do does doing down during each few for from further had has
    have having he her here hers him his how i if in into is it its just like me more most my no nor
    not now of off on once only or other our out over own remind same she should so some such than
    that the their them then there these they this those through to too under until up us very was
    we were what when where which while who whom why will with would you your yours hey hi hello
    please tell know want need get got let say said
    hai hain ho hoon hun tha thi the ka ki ke ko se me mein mai main mujhe mera meri mere tum tumhe
    aap aapka kya kaun kaise kab kahan kyun kyu aur ya par pe bhi toh to ye yeh woh wo is us isko usko
    nahi nahin na hi tha thi hoga hogi karo kar karna kiya kuch koi
    """.split()
)

_TOKEN = re.compile(r"[\w@.+:/-]+", re.UNICODE)
MAX_KEYWORDS = 16


def tokenize(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKC", str(text)).lower()
    out: list[str] = []
    for tok in _TOKEN.findall(folded):
        tok = tok.strip(".:/-+@")
        if tok:
            out.append(tok)
    return out


def keywords(text: str, *, limit: int = MAX_KEYWORDS) -> list[str]:
    """Content words, de-duplicated, order preserved. Falls back to every token
    when the sentence is nothing but stopwords so short queries still work."""
    toks = tokenize(text)
    content = [t for t in toks if t not in _STOPWORDS and len(t) >= 2]
    chosen = content or []
    return list(dict.fromkeys(chosen))[:limit]


def prefix_overlap(query_terms: list[str], text: str) -> int:
    """How many query terms are matched (as word prefixes) by `text`."""
    words = set(tokenize(text))
    n = 0
    for term in query_terms:
        stem = term[:-1] if len(term) > 4 and term.endswith("s") else term
        if any(w.startswith(stem) or stem.startswith(w) and len(w) >= 4 for w in words):
            n += 1
    return n
