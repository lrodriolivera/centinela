"""LangGraph orchestrator — routes user intent to specialized agents.

Architecture:
    User Input → Router (classifies intent) → Specialized Agent → Response

Intent categories:
    - code: Read/write/edit code → CoderAgent
    - research: Web search, RAG, docs → ResearcherAgent
    - execute: Shell commands → ExecutorAgent
    - review: Review code/results → ReviewerAgent
    - general: Generic questions → BaseAgent (direct LLM)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from centinela.agents.base import BaseAgent
from centinela.agents.coder import CoderAgent
from centinela.agents.executor import ExecutorAgent
from centinela.agents.researcher import ResearcherAgent
from centinela.agents.reviewer import ReviewerAgent
from centinela.core.config import CentinelaConfig, get_config
from centinela.core.memory import get_memory_manager
from centinela.core.models import ModelResolver, get_model_resolver

logger = logging.getLogger(__name__)

# Intent classification prompt — fast and cheap (uses smallest model)
_ROUTER_PROMPT = """\
Clasifica la intención del usuario en exactamente UNA categoría.
Responde SOLO con la categoría, sin explicación.

Categorías:
- code: El usuario quiere leer, escribir, editar o crear archivos de código
- research: El usuario quiere buscar información, documentación o investigar algo
- execute: El usuario quiere ejecutar un comando shell o script
- review: El usuario quiere revisar, validar o auditar código o resultados
- general: Pregunta general, conversación, o no encaja en las anteriores

Mensaje del usuario: {message}

Categoría:"""


class Orchestrator:
    """Routes user messages to the appropriate specialized agent."""

    def __init__(
        self,
        config: CentinelaConfig | None = None,
        model_resolver: ModelResolver | None = None,
    ):
        self.config = config or get_config()
        self.resolver = model_resolver or get_model_resolver()
        self.memory = get_memory_manager()

        # Initialize agents
        self._agents: dict[str, BaseAgent] = {
            "code": CoderAgent(config=self.config, model_resolver=self.resolver),
            "research": ResearcherAgent(config=self.config, model_resolver=self.resolver),
            "execute": ExecutorAgent(config=self.config, model_resolver=self.resolver),
            "review": ReviewerAgent(config=self.config, model_resolver=self.resolver),
            "general": BaseAgent(config=self.config, model_resolver=self.resolver),
        }

    def _classify_intent(self, message: str) -> str:
        """Classify user intent using the fastest/cheapest model."""
        prompt = _ROUTER_PROMPT.format(message=message)

        # Use Haiku (cheapest) for routing
        haiku_id = self.config.models.fallbacks[-1] if self.config.models.fallbacks else None
        try:
            response = self.resolver.invoke(
                messages=[{"role": "user", "content": prompt}],
                model=haiku_id,
                max_tokens=20,
                temperature=0.0,
            )
            intent = response.text.strip().lower()
            # Validate it's a known intent
            if intent in self._agents:
                return intent
        except Exception as e:
            logger.warning("Intent classification failed: %s", e)

        return "general"

    def chat(self, message: str) -> str:
        """Route message to the appropriate agent and return the response."""
        # Record user message
        self.memory.record_interaction(role="user", content=message)

        # Classify intent
        intent = self._classify_intent(message)
        agent = self._agents[intent]
        logger.info("Routed to '%s' agent (intent: %s)", agent.name, intent)

        # Get response
        response = agent.chat(message)

        # Record assistant response
        self.memory.record_interaction(
            role="assistant",
            content=response,
            agent_id=agent.name,
        )

        return response

    def stream_chat(self, message: str) -> Iterator[str]:
        """Route message and stream the response."""
        # Record user message
        self.memory.record_interaction(role="user", content=message)

        # Classify intent
        intent = self._classify_intent(message)
        agent = self._agents[intent]
        logger.info("Routed to '%s' agent (intent: %s)", agent.name, intent)

        # Yield agent indicator
        agent_label = {
            "code": "coder",
            "research": "researcher",
            "execute": "executor",
            "review": "reviewer",
            "general": "centinela",
        }.get(intent, "centinela")
        yield f"[{agent_label}] "

        # Stream response
        full_response = ""
        for chunk in agent.stream_chat(message):
            full_response += chunk
            yield chunk

        # Record full response
        self.memory.record_interaction(
            role="assistant",
            content=full_response,
            agent_id=agent.name,
        )

    def reset(self) -> None:
        """Reset all agents' conversation histories."""
        for agent in self._agents.values():
            agent.reset()

    def get_status(self) -> dict:
        """Get orchestrator status."""
        return {
            "agents": {name: {
                "name": agent.name,
                "description": agent.description,
                "permission_tier": agent.permission_tier.value,
                "history_length": len(agent.history),
            } for name, agent in self._agents.items()},
            "memory": self.memory.episodic.get_stats(),
            "models": self.resolver.get_status(),
        }


# Global instance
_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator()
    return _orchestrator


def reset_orchestrator() -> None:
    global _orchestrator
    _orchestrator = None
