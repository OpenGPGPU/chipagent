"""Indexer for knowledge base - scans skill references and examples."""
from pathlib import Path
from typing import List, Dict, Tuple
import re


class KnowledgeIndexer:
    """Scans chipagent knowledge sources and extracts chunks."""

    def __init__(self, chipagent_root: Path):
        self.chipagent_root = chipagent_root
        self.skills_dir = chipagent_root / "chipagent" / "skills" / "text"

    def scan(self) -> List[Dict[str, str]]:
        """Scan all knowledge sources and return chunks."""
        chunks = []

        # Scan skill references and examples
        if self.skills_dir.exists():
            for skill_dir in self.skills_dir.iterdir():
                if skill_dir.is_dir():
                    chunks.extend(self._scan_skill(skill_dir))

        # Scan main documentation
        docs_dir = self.chipagent_root
        for doc_file in ["README.md", "CLAUDE_CODE_INTEGRATION.md",
                        "ChipAgent_Phase1_Implementation_Plan.md",
                        "ChipAgent_Phase2_Implementation_Plan.md",
                        "ChipAgent_Phase3_Implementation_Plan.md"]:
            doc_path = docs_dir / doc_file
            if doc_path.exists():
                chunks.extend(self._scan_document(doc_path, "documentation"))

        return chunks

    def _scan_skill(self, skill_dir: Path) -> List[Dict[str, str]]:
        """Scan a single skill directory."""
        chunks = []
        skill_name = skill_dir.name

        # Scan SKILL.md
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            content = skill_md.read_text(encoding="utf-8")
            chunks.append({
                "source": str(skill_md),
                "type": "skill_description",
                "skill": skill_name,
                "content": content,
            })

        # Scan reference/ directory
        ref_dir = skill_dir / "reference"
        if ref_dir.exists():
            for ref_file in ref_dir.iterdir():
                if ref_file.is_file():
                    content = ref_file.read_text(encoding="utf-8", errors="ignore")
                    chunks.append({
                        "source": str(ref_file),
                        "type": "skill_reference",
                        "skill": skill_name,
                        "filename": ref_file.name,
                        "content": content,
                    })

        # Scan examples/ directory
        ex_dir = skill_dir / "examples"
        if ex_dir.exists():
            for ex_file in ex_dir.iterdir():
                if ex_file.is_file():
                    content = ex_file.read_text(encoding="utf-8", errors="ignore")
                    chunks.append({
                        "source": str(ex_file),
                        "type": "skill_example",
                        "skill": skill_name,
                        "filename": ex_file.name,
                        "content": content,
                    })

        return chunks

    def _scan_document(self, doc_path: Path, doc_type: str) -> List[Dict[str, str]]:
        """Scan a markdown document and split into chunks."""
        chunks = []
        content = doc_path.read_text(encoding="utf-8")

        # Split by markdown headers
        sections = re.split(r'\n(?=##\s)', content)

        for i, section in enumerate(sections):
            if section.strip():
                chunks.append({
                    "source": str(doc_path),
                    "type": doc_type,
                    "section": i,
                    "content": section.strip(),
                })

        return chunks
