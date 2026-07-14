"""Embedding service using local sentence-transformers (bge-small-en-v1.5)."""
import os
import logging
from typing import List

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] = "0.0"

import torch
torch.set_num_threads(1)

from sentence_transformers import SentenceTransformer
from config import get_settings

logger = logging.getLogger(__name__)

_model = None


def get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        settings = get_settings()
        logger.info(f"Loading embedding model: {settings.EMBEDDING_MODEL}")
        _model = SentenceTransformer(settings.EMBEDDING_MODEL, device="cpu")
    return _model


def embed_text(text: str) -> List[float]:
    """Embed a single text string."""
    model = get_model()
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def embed_texts(texts: List[str], batch_size: int = 32) -> List[List[float]]:
    """Embed multiple texts in batches."""
    model = get_model()
    embeddings = model.encode(texts, batch_size=batch_size, normalize_embeddings=True)
    return embeddings.tolist()
