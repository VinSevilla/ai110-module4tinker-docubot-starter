"""
Core DocuBot class responsible for:
- Loading documents from the docs/ folder
- Building a simple retrieval index (Phase 1)
- Retrieving relevant snippets (Phase 1)
- Supporting retrieval only answers
- Supporting RAG answers when paired with Gemini (Phase 2)
"""

import os
import glob
import re

# Matches the whitespace between two sentences (letter, then ./!/?, then a
# space) without matching after list markers like "1." or "2.".
SENTENCE_BOUNDARY_RE = re.compile(r'(?<=[a-zA-Z][.!?])\s+')
# Matches a bullet ("-", "*") or numbered ("1.") list marker at line start.
LIST_ITEM_RE = re.compile(r'^(-|\*|\d+\.)\s')
# Lowercase alphanumeric runs. Splits on punctuation AND separators like
# "_"/"."/"/" so compound identifiers (e.g. `auth_utils.py`) decompose into
# their meaningful parts ("auth", "utils", "py") instead of staying one token.
WORD_RE = re.compile(r'[a-z0-9]+')

class DocuBot:
    def __init__(self, docs_folder="docs", llm_client=None):
        """
        docs_folder: directory containing project documentation files
        llm_client: optional Gemini client for LLM based answers
        """
        self.docs_folder = docs_folder
        self.llm_client = llm_client

        # Load documents into memory
        self.documents = self.load_documents()  # List of (filename, text)

        # Build a retrieval index (implemented in Phase 1)
        self.index = self.build_index(self.documents)

    # -----------------------------------------------------------
    # Document Loading
    # -----------------------------------------------------------

    def load_documents(self):
        """
        Loads all .md and .txt files inside docs_folder.
        Each file is split into paragraphs, then further into smaller
        sections (individual list items, individual sentences) so
        retrieval can pinpoint narrower spans of text.
        Returns a list of tuples: (filename, text)
        """
        docs = []
        base = os.path.dirname(os.path.abspath(__file__))
        pattern = os.path.join(base, self.docs_folder, "*.*")
        for path in glob.glob(pattern):
            if path.endswith(".md") or path.endswith(".txt"):
                with open(path, "r", encoding="utf8") as f:
                    text = f.read()
                filename = os.path.basename(path)
                for paragraph in text.split("\n\n"):
                    paragraph = paragraph.strip()
                    if not paragraph:
                        continue
                    for section in self._split_into_sections(paragraph):
                        docs.append((filename, section))
        return docs

    def _split_into_sections(self, paragraph):
        """
        Break a paragraph into smaller sections: individual items when it's
        a flat bullet/numbered list, then individual sentences within each
        resulting unit. Fenced code blocks are kept intact since splitting
        them line by line would break their meaning.
        """
        if paragraph.startswith("```"):
            return [paragraph]

        lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
        if len(lines) > 1 and all(LIST_ITEM_RE.match(line) for line in lines):
            units = lines
        else:
            units = [paragraph]

        sections = []
        for unit in units:
            sections.extend(s.strip() for s in SENTENCE_BOUNDARY_RE.split(unit) if s.strip())
        return sections or [paragraph]

    # -----------------------------------------------------------
    # Index Construction (Phase 1)
    # -----------------------------------------------------------

    def build_index(self, documents):
        """
        Build an inverted index mapping each meaningful lowercase word to the
        positions (indices into `documents`) of the chunks that contain it as
        a whole word. Used by retrieve() to narrow down candidate chunks
        before scoring.
        """
        index = {}
        for i, (_, text) in enumerate(documents):
            for word in self._meaningful_words(text):
                index.setdefault(word, set()).add(i)
        return index

    # -----------------------------------------------------------
    # Scoring and Retrieval (Phase 1)
    # -----------------------------------------------------------

    STOP_WORDS = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "to", "of", "in", "on",
        "at", "for", "with", "by", "from", "and", "or", "but", "not", "it",
        "its", "this", "that", "these", "those", "what", "which", "who",
        "how", "when", "where", "why", "there", "their", "they", "them",
        "like", "just", "about", "any", "i", "my", "we", "our", "you",
        "your", "he", "she", "his", "her", "today", "s"
    }

    def _meaningful_words(self, text):
        """
        Extract the lowercase words that matter for scoring/indexing:
        alphanumeric runs (so compound identifiers like `auth_utils.py`
        decompose into "auth", "utils", "py"), with stop words filtered out.
        """
        words = WORD_RE.findall(text.lower())
        return [w for w in words if w not in self.STOP_WORDS]

    def score_document(self, query, text):
        """
        Return a simple relevance score for how well the text matches the query:
        the number of distinct meaningful query words that appear as whole
        words in the text (not merely as substrings of longer words).
        """
        meaningful = self._meaningful_words(query)
        if not meaningful:
            return 0
        text_words = set(self._meaningful_words(text))
        return sum(1 for word in meaningful if word in text_words)

    def retrieve(self, query, top_k=3, min_score=1):
        """
        Use the index to find candidate chunks that share at least one
        meaningful word with the query, score just those candidates, and
        return the top_k highest scoring (filename, text) snippets.
        """
        meaningful = self._meaningful_words(query)
        if not meaningful:
            return []

        candidate_positions = set()
        for word in meaningful:
            candidate_positions.update(self.index.get(word, ()))

        scored = []
        for i in sorted(candidate_positions):
            filename, text = self.documents[i]
            score = self.score_document(query, text)
            if score >= min_score:
                scored.append((score, filename, text))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [(filename, text) for _, filename, text in scored[:top_k]]

    def has_sufficient_evidence(self, query, snippets):
        """
        Guardrail: require the best-matching snippet to cover at least half
        of the query's meaningful words (rounded up), so a single incidental
        keyword overlap isn't treated as enough evidence to answer.
        """
        if not snippets:
            return False
        meaningful = self._meaningful_words(query)
        if not meaningful:
            return False
        best_score = max(self.score_document(query, text) for _, text in snippets)
        required = max(1, (len(meaningful) + 1) // 2)
        return best_score >= required

    # -----------------------------------------------------------
    # Answering Modes
    # -----------------------------------------------------------

    def answer_retrieval_only(self, query, top_k=3):
        """
        Phase 1 retrieval only mode.
        Returns raw snippets and filenames with no LLM involved.
        """
        snippets = self.retrieve(query, top_k=top_k)

        if not self.has_sufficient_evidence(query, snippets):
            return "I do not know based on these docs."

        formatted = []
        for filename, text in snippets:
            formatted.append(f"[{filename}]\n{text}\n")

        return "\n---\n".join(formatted)

    def answer_rag(self, query, top_k=3):
        """
        Phase 2 RAG mode.
        Uses student retrieval to select snippets, then asks Gemini
        to generate an answer using only those snippets.
        """
        if self.llm_client is None:
            raise RuntimeError(
                "RAG mode requires an LLM client. Provide a GeminiClient instance."
            )

        snippets = self.retrieve(query, top_k=top_k)

        if not self.has_sufficient_evidence(query, snippets):
            return "I do not know based on these docs."

        print("\n[DEBUG] Snippets retrieved for RAG:")
        for fname, chunk in snippets:
            print(f"  [{fname}] {chunk[:120]}")

        return self.llm_client.answer_from_snippets(query, snippets)

    # -----------------------------------------------------------
    # Bonus Helper: concatenated docs for naive generation mode
    # -----------------------------------------------------------

    def full_corpus_text(self):
        """
        Returns all documents concatenated into a single string.
        This is used in Phase 0 for naive 'generation only' baselines.
        """
        return "\n\n".join(text for _, text in self.documents)
