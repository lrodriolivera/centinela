"""RAG tools — document indexing and semantic search.

Uses Qdrant (embedded mode) for vector storage and
Bedrock Titan Embeddings for generating vectors.
Supports PDF, Markdown, and plain text files.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from centinela.core.config import get_config
from centinela.tools.registry import PermissionTier, get_tool_registry

logger = logging.getLogger(__name__)

registry = get_tool_registry()

_COLLECTION_NAME = "centinela_docs"
_CHUNK_SIZE = 1000
_CHUNK_OVERLAP = 150


def _chunk_text(text: str, chunk_size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]
        # Try to break at a paragraph or sentence boundary
        if end < len(text):
            for sep in ["\n\n", "\n", ". ", " "]:
                last_sep = chunk.rfind(sep)
                if last_sep > chunk_size * 0.5:
                    chunk = chunk[: last_sep + len(sep)]
                    end = start + len(chunk)
                    break
        chunks.append(chunk.strip())
        start = end - overlap
    return [c for c in chunks if c]


def _read_document(path: Path) -> str:
    """Read a document file and return its text."""
    suffix = path.suffix.lower()

    if suffix == ".pdf":
        try:
            import subprocess
            result = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return result.stdout
        except FileNotFoundError:
            pass
        return f"[No se pudo leer PDF: {path.name}. Instala poppler-utils]"

    if suffix in (".md", ".txt", ".rst", ".csv", ".json", ".yaml", ".yml", ".py", ".js", ".ts"):
        return path.read_text(encoding="utf-8", errors="replace")

    return f"[Formato no soportado: {suffix}]"


def _get_embeddings(texts: list[str]) -> list[list[float]] | None:
    """Generate embeddings via Bedrock Titan."""
    try:
        import boto3
        config = get_config()
        session = boto3.Session(profile_name=config.models.aws_profile)
        client = session.client("bedrock-runtime", region_name=config.models.region)

        embeddings = []
        for text in texts:
            response = client.invoke_model(
                modelId="amazon.titan-embed-text-v2:0",
                body=json.dumps({"inputText": text[:8000]}),
                contentType="application/json",
            )
            result = json.loads(response["body"].read())
            embeddings.append(result["embedding"])
        return embeddings
    except Exception as e:
        logger.warning("Embeddings generation failed: %s", e)
        return None


def _get_qdrant_client():
    """Get Qdrant client in embedded mode."""
    from qdrant_client import QdrantClient

    config = get_config()
    qdrant_path = config.qdrant_path
    qdrant_path.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(qdrant_path))


def _ensure_collection(client, vector_size: int = 1024):
    """Create collection if it doesn't exist."""
    from qdrant_client.models import Distance, VectorParams

    collections = [c.name for c in client.get_collections().collections]
    if _COLLECTION_NAME not in collections:
        client.create_collection(
            collection_name=_COLLECTION_NAME,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        logger.info("Created Qdrant collection '%s'", _COLLECTION_NAME)


@registry.register(
    name="index_document",
    description=(
        "Indexa un documento del workspace para búsqueda semántica (RAG). "
        "Soporta archivos .txt, .md, .py, .pdf. El documento se divide en "
        "chunks y se almacena con embeddings vectoriales."
    ),
    permission=PermissionTier.WRITE,
    tags=["rag", "memory"],
)
def index_document(path: str) -> str:
    """Index a document for semantic search."""
    config = get_config()
    workspace = config.workspace_path
    resolved = (workspace / path).resolve()

    if not str(resolved).startswith(str(workspace)):
        return f"Error: '{path}' está fuera del workspace."
    if not resolved.is_file():
        return f"Error: '{path}' no existe."

    # Read and chunk
    text = _read_document(resolved)
    if text.startswith("["):
        return text  # Error message
    chunks = _chunk_text(text)
    if not chunks:
        return f"Error: documento vacío o no se pudo extraer texto de '{path}'."

    # Generate embeddings
    embeddings = _get_embeddings(chunks)
    if embeddings is None:
        return "Error: no se pudieron generar embeddings. Verifica la conexión a Bedrock."

    # Store in Qdrant
    client = _get_qdrant_client()
    _ensure_collection(client, vector_size=len(embeddings[0]))

    from qdrant_client.models import PointStruct

    points = []
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        doc_hash = hashlib.md5(f"{path}:{i}".encode()).hexdigest()
        point_id = int(doc_hash[:12], 16)
        points.append(PointStruct(
            id=point_id,
            vector=embedding,
            payload={
                "text": chunk,
                "source": path,
                "chunk_index": i,
                "total_chunks": len(chunks),
            },
        ))

    client.upsert(collection_name=_COLLECTION_NAME, points=points)
    return f"Indexado: {path} ({len(chunks)} chunks, {len(text)} caracteres)"


@registry.register(
    name="search_knowledge",
    description=(
        "Busca información en los documentos indexados usando búsqueda semántica. "
        "Retorna los fragmentos más relevantes basados en similitud de significado."
    ),
    permission=PermissionTier.READ,
    tags=["rag", "memory"],
)
def search_knowledge(query: str, top_k: int = 5) -> str:
    """Search indexed documents by semantic similarity."""
    embeddings = _get_embeddings([query])
    if embeddings is None:
        return "Error: no se pudo generar embedding para la query."

    client = _get_qdrant_client()

    try:
        results = client.query_points(
            collection_name=_COLLECTION_NAME,
            query=embeddings[0],
            limit=top_k,
        ).points
    except Exception as e:
        return f"Error en búsqueda: {e}"

    if not results:
        return "Sin resultados. ¿Has indexado documentos con 'index_document'?"

    output_parts = [f"# {len(results)} resultados para: '{query}'\n"]
    for i, point in enumerate(results, 1):
        payload = point.payload
        score = point.score
        source = payload.get("source", "?")
        chunk_idx = payload.get("chunk_index", "?")
        text = payload.get("text", "")
        output_parts.append(
            f"## [{i}] {source} (chunk {chunk_idx}, score: {score:.3f})\n{text}\n"
        )

    return "\n".join(output_parts)
