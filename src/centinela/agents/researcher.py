"""Researcher agent — specialized in web research and RAG search."""

from __future__ import annotations

from centinela.agents.base import BaseAgent
from centinela.tools.registry import PermissionTier

RESEARCHER_SYSTEM_PROMPT = """\
Eres el agente Researcher de Centinela. Tu especialidad es investigar información.

Capacidades:
- Buscar en documentos indexados con search_knowledge
- Indexar nuevos documentos con index_document
- Acceder a URLs con web_fetch (requiere aprobación)
- Buscar en archivos locales con search_files

Reglas:
- Prioriza la información de documentos indexados (RAG) sobre búsqueda web.
- Cita las fuentes de tu información (archivo, URL, chunk).
- Si no encuentras información suficiente, dilo honestamente.
- Nunca inventes datos ni cites fuentes ficticias.
- Resume la información de forma clara y concisa.
"""


class ResearcherAgent(BaseAgent):
    name = "researcher"
    description = "Agente especializado en investigación web y búsqueda en documentos"
    system_prompt = RESEARCHER_SYSTEM_PROMPT
    permission_tier = PermissionTier.EXECUTE
