"""Optional local sentence-transformers embedding provider."""

from collections.abc import Sequence


class SentenceTransformerEmbedder:
    def __init__(self, model_id: str, local_files_only: bool = False):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RuntimeError(
                "Install the semantic extra with `pip install -e '.[semantic]'` "
                "to enable sentence-transformer embeddings."
            ) from error
        self.model_id = model_id
        self._model = SentenceTransformer(model_id, device="cpu", local_files_only=local_files_only)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vectors.tolist()


class SentenceTransformerReranker:
    def __init__(self, model_id: str, local_files_only: bool = False):
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as error:
            raise RuntimeError(
                "Install the semantic extra with `pip install -e '.[semantic]'` "
                "to enable cross-encoder reranking."
            ) from error
        self.model_id = model_id
        self._model = CrossEncoder(model_id, device="cpu", local_files_only=local_files_only)

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        pairs = [(query, document) for document in documents]
        scores = self._model.predict(pairs, show_progress_bar=False)
        return [float(score) for score in scores]
