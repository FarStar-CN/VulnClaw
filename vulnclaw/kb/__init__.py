"""VulnClaw knowledge-base package."""

from importlib import import_module

__all__ = [
    "KnowledgeStore",
    "KnowledgeRetriever",
    "KeywordRetriever",
    "RetrieverStatus",
    "ExperienceStore",
    "Lesson",
    "LessonTags",
    "EvidenceRefs",
    "LessonScope",
    "LessonStatus",
    "LessonSignal",
]


def __getattr__(name: str):
    """Load the requested public component without importing retrieval engines."""
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if name in {"KnowledgeRetriever", "KeywordRetriever", "RetrieverStatus"}:
        module = "retriever"
    elif name == "KnowledgeStore":
        module = "store"
    else:
        module = "experience"
    value = getattr(import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value
