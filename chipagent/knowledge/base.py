"""KnowledgeBase - main interface for knowledge retrieval."""
from pathlib import Path
from typing import List, Dict, Tuple
from .embedder import SimpleEmbedder
from .indexer import KnowledgeIndexer
from .retriever import Retriever


class KnowledgeBase:
    """Main knowledge base interface."""

    def __init__(self, chipagent_root: Path = None):
        if chipagent_root is None:
            # Default to chipagent package root
            chipagent_root = Path(__file__).parent.parent.parent

        self.chipagent_root = chipagent_root
        self.embedder = SimpleEmbedder()
        self.retriever = Retriever(self.embedder)
        self._indexed = False

    def build_index(self):
        """Build index from knowledge sources."""
        indexer = KnowledgeIndexer(self.chipagent_root)
        chunks = indexer.scan()
        self.retriever.index(chunks)
        self._indexed = True

    def query(self, query: str, top_k: int = 5) -> List[Dict]:
        """Query knowledge base and return relevant chunks."""
        if not self._indexed:
            self.build_index()

        results = self.retriever.retrieve(query, top_k)

        return [
            {
                "source": chunk["source"],
                "type": chunk["type"],
                "skill": chunk.get("skill"),
                "content": chunk["content"],
                "score": score,
            }
            for chunk, score in results
            if score > 0.01  # Filter out very low scores
        ]

    def search_examples(self, description: str, top_k: int = 3) -> List[Dict]:
        """Search for code examples matching description."""
        if not self._indexed:
            self.build_index()

        results = self.retriever.retrieve(description, top_k=top_k * 3)

        # Filter to examples only
        examples = [
            {
                "source": chunk["source"],
                "skill": chunk.get("skill"),
                "filename": chunk.get("filename"),
                "content": chunk["content"],
                "score": score,
            }
            for chunk, score in results
            if chunk.get("type") == "skill_example" and score > 0.01
        ]

        return examples[:top_k]

    def get_skill_info(self, skill_name: str) -> Dict:
        """Get information about a specific skill."""
        if not self._indexed:
            self.build_index()

        # Find skill description
        for chunk in self.retriever.chunks:
            if chunk.get("skill") == skill_name and chunk.get("type") == "skill_description":
                return {
                    "skill": skill_name,
                    "description": chunk["content"],
                    "source": chunk["source"],
                }

        return {"skill": skill_name, "description": "Skill not found"}
