"""BM25 keyword index with character bigram tokenizer"""
from __future__ import annotations

from typing import Optional

from rank_bm25 import BM25Okapi


class BM25Index:
    """BM25 keyword search index using character bigram tokenization.

    Stores (pg_id, text) pairs so that search results carry PostgreSQL
    primary keys, enabling correct RRF fusion with vector search results.

    Usage::

        idx = BM25Index("my_collection")
        idx.build([(1, "text one"), (2, "text two")])
        results = idx.search("query text", top_k=20)
        # results: [(pg_id, score), ...]
    """

    def __init__(self, collection_name: str) -> None:
        """Initialize BM25 index instance.

        Args:
            collection_name: Collection name for logging / debugging.
        """
        self._collection_name = collection_name
        self._corpus: list[str] = []
        self._ids: list[int] = []
        self._metas: list[dict] = []
        self._index: Optional[BM25Okapi] = None

    def build(
        self,
        items: list[tuple[int, str]] | list[tuple[int, str, dict]] | None,
    ) -> None:
        """Build BM25Okapi index from (pg_id, text[, metadata]) pairs.

        Args:
            items: List of (pg_id, text) or (pg_id, text, metadata) pairs.
                None or empty list clears the index.
        """
        if not items:
            self._corpus = []
            self._ids = []
            self._metas = []
            self._index = None
            return

        self._ids = [item[0] for item in items]
        self._corpus = [item[1] for item in items]
        self._metas = [item[2] if len(item) > 2 else {} for item in items]
        tokenized = [self._tokenize(t) for t in self._corpus]
        self._index = BM25Okapi(tokenized)

    def search(self, query: str, top_k: int = 20) -> list[tuple[int, float]]:
        """Search the index and return top_k (pg_id, score) pairs.

        Args:
            query: Raw query string.
            top_k: Maximum number of results.

        Returns:
            List of (pg_id, bm25_score) sorted by score descending.
            Empty list if the index is not built.
        """
        if self._index is None:
            return []

        tokenized_query = self._tokenize(query)
        scores = self._index.get_scores(tokenized_query)

        # Pair (pg_id, score) and sort by score descending
        scored = [(self._ids[i], float(s)) for i, s in enumerate(scores)]
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[:top_k]

    def get_text(self, pg_id: int) -> str:
        """Get original corpus text by PG primary key.

        Args:
            pg_id: PostgreSQL primary key of the document.

        Returns:
            Original text string, or empty string if pg_id is not found.
        """
        try:
            idx = self._ids.index(pg_id)
            return self._corpus[idx]
        except ValueError:
            return ""

    def get_metadata(self, pg_id: int) -> dict:
        """Get metadata dict by PG primary key.

        Args:
            pg_id: PostgreSQL primary key of the document.

        Returns:
            Metadata dict, or {} if pg_id is not found.
        """
        try:
            idx = self._ids.index(pg_id)
            return self._metas[idx]
        except ValueError:
            return {}

    @property
    def corpus_size(self) -> int:
        """Return the number of documents in the corpus."""
        return len(self._corpus)

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text into character bigrams.

        For text shorter than 2 characters, returns the characters as-is
        to avoid producing an empty token list that would break BM25Okapi.

        Args:
            text: Raw text to tokenize.

        Returns:
            List of character bigram tokens.
        """
        chars = list(text)
        if len(chars) < 2:
            return chars
        return [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
