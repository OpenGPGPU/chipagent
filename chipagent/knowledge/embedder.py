"""Simple TF-IDF based embedder for knowledge base."""
import math
import re
from collections import Counter
from typing import List, Dict


class SimpleEmbedder:
    """Lightweight TF-IDF embedder with no external dependencies."""

    def __init__(self):
        self.vocab: List[str] = []
        self.idf: Dict[str, float] = {}

    def tokenize(self, text: str) -> List[str]:
        """Simple tokenization: lowercase, split on non-alphanumeric, remove short tokens."""
        tokens = re.findall(r'\w+', text.lower())
        return [t for t in tokens if len(t) > 2]

    def fit(self, documents: List[str]):
        """Build vocabulary and compute IDF from documents."""
        # Collect all unique tokens
        all_tokens = set()
        doc_freq = Counter()

        for doc in documents:
            tokens = set(self.tokenize(doc))
            all_tokens.update(tokens)
            for token in tokens:
                doc_freq[token] += 1

        self.vocab = sorted(all_tokens)
        n_docs = len(documents)

        # Compute IDF
        for token in self.vocab:
            df = doc_freq.get(token, 1)
            self.idf[token] = math.log(n_docs / df) + 1  # smoothed IDF

    def embed(self, text: str) -> Dict[str, float]:
        """Embed text as sparse TF-IDF vector."""
        if not self.vocab:
            return {}

        tokens = self.tokenize(text)
        tf = Counter(tokens)
        n_tokens = len(tokens) if tokens else 1

        # Compute TF-IDF
        vector = {}
        for token, count in tf.items():
            if token in self.idf:
                tf_val = count / n_tokens
                tfidf = tf_val * self.idf[token]
                vector[token] = tfidf

        return vector

    def similarity(self, vec1: Dict[str, float], vec2: Dict[str, float]) -> float:
        """Compute cosine similarity between two sparse vectors."""
        if not vec1 or not vec2:
            return 0.0

        # Dot product
        common_keys = set(vec1.keys()) & set(vec2.keys())
        dot_product = sum(vec1[k] * vec2[k] for k in common_keys)

        # Magnitudes
        mag1 = math.sqrt(sum(v * v for v in vec1.values()))
        mag2 = math.sqrt(sum(v * v for v in vec2.values()))

        if mag1 == 0 or mag2 == 0:
            return 0.0

        return dot_product / (mag1 * mag2)
