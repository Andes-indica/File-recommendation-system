"""Optional local sentence-transformers embedding provider."""

from collections.abc import Sequence


class SentenceTransformerEmbedder:
    def __init__(self, model_id: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise RuntimeError(
                "Install the semantic extra with `pip install -e '.[semantic]'` "
                "to enable sentence-transformer embeddings."
            ) from error
        self.model_id = model_id
        self._model = SentenceTransformer(model_id)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vectors.tolist()
