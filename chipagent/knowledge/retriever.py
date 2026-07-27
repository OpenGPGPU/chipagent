"""Retriever for knowledge base - hybrid vector + keyword search."""
from typing import List, Dict, Tuple
from .embedder import SimpleEmbedder


class Retriever:
    """Hybrid retriever combining TF-IDF similarity and keyword matching."""

    def __init__(self, embedder: SimpleEmbedder):
        self.embedder = embedder
        self.chunks: List[Dict[str, str]] = []
        self.chunk_vectors: List[Dict[str, float]] = []

    def index(self, chunks: List[Dict[str, str]]):
        """Index chunks for retrieval."""
        self.chunks = chunks

        # Build TF-IDF model
        documents = [chunk["content"] for chunk in chunks]
        self.embedder.fit(documents)

        # Embed all chunks
        self.chunk_vectors = [self.embedder.embed(doc) for doc in documents]

    def retrieve(self, query: str, top_k: int = 5) -> List[Tuple[Dict[str, str], float]]:
        """Retrieve top-k most relevant chunks for query."""
        if not self.chunks:
            return []

        # Embed query
        query_vec = self.embedder.embed(query)

        # Compute similarities
        scored_chunks = []
        for i, (chunk, chunk_vec) in enumerate(zip(self.chunks, self.chunk_vectors)):
            # Vector similarity
            vec_sim = self.embedder.similarity(query_vec, chunk_vec)

            # Keyword matching boost
            keyword_score = self._keyword_match(query, chunk["content"])

            # Combined score (weighted)
            combined_score = 0.7 * vec_sim + 0.3 * keyword_score

            scored_chunks.append((chunk, combined_score))

        # Sort by score descending
        scored_chunks.sort(key=lambda x: x[1], reverse=True)

        return scored_chunks[:top_k]

    def _keyword_match(self, query: str, content: str) -> float:
        """Simple keyword matching score."""
        query_tokens = set(self.embedder.tokenize(query))
        content_tokens = set(self.embedder.tokenize(content))

        if not query_tokens:
            return 0.0

        matches = len(query_tokens & content_tokens)
        return matches / len(query_tokens)
