"""Small-corpus BM25 and reciprocal-rank fusion. Scores are not confidence probabilities."""
from collections import Counter
import math
import re


def terms(text):
    return re.findall(r"[^\W_]+(?:[-_.][^\W_]+)*", text.casefold(), re.UNICODE)


def bm25(query, documents):
    tokens = {key: terms(value) for key, value in documents.items()}
    if not tokens:
        return []
    average = sum(map(len, tokens.values())) / len(tokens) or 1
    frequencies = Counter(term for words in tokens.values() for term in set(words))
    scores = []
    for key, words in tokens.items():
        counts = Counter(words)
        score = 0.0
        for term in set(terms(query)):
            tf = counts[term]
            if tf:
                idf = math.log(1 + (len(tokens) - frequencies[term] + .5) / (frequencies[term] + .5))
                score += idf * tf * 2.5 / (tf + 1.5 * (.25 + .75 * len(words) / average))
        if score > 0:
            scores.append((key, score))
    return sorted(scores, key=lambda item: (-item[1], item[0]))


def fuse(vector_ids, keyword_ids, constant=60):
    scores = Counter()
    for ranking in (vector_ids, keyword_ids):
        for rank, key in enumerate(dict.fromkeys(ranking), 1):
            scores[key] += 1 / (constant + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
