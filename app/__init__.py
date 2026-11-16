"""News ingestion agent.

Package layout:
    config      settings + source registry
    models      dataclasses shared across layers
    net         HTTP fetching with retries and size limits
    adapters    RSS / HTML feed adapters
    extract     article body + attachment extraction
    documents   attachment download and hashing
    store       SQLite persistence and deduplication
    lark        Lark Base sink (dry-run capable)
    llm         Ollama / mock enrichment client
    agent       smolagents tool-calling agent
    pipeline    orchestration of the daily run
"""

__version__ = "1.0.0"
