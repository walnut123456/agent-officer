"""Table RAG package with lazy loading for optional NLP dependencies."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermes_officer.tool.table_rag.table_rag import TableRAGAgent

__all__ = ["TableRAGAgent"]


def __getattr__(name: str):
    if name == "TableRAGAgent":
        from hermes_officer.tool.table_rag.table_rag import TableRAGAgent

        return TableRAGAgent
    raise AttributeError(name)
