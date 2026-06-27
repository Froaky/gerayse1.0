# Gerayse AI Agent Pack

Archivos incluidos para ordenar desarrollo asistido por IA:

- `AGENTS.md`: reemplazo del archivo raiz actual. Es la instruccion principal para agentes.
- `CLAUDE.md`: wrapper para Claude Code que remite a `AGENTS.md`.
- `.cursor/rules/gerayse-agent.mdc`: reglas para Cursor.
- `.github/pull_request_template.md`: checklist de PR con foco en SOLID, datos y tests.
- `docs/ai/AI_CHANGE_PROTOCOL.md`: protocolo para trabajar por slices aislados.
- `docs/ai/ARCHITECTURE_MAP.md`: mapa de dominios, capas y fuentes de verdad.
- `docs/ai/TESTING_SAFETY_MATRIX.md`: matriz para elegir pruebas proporcionales al riesgo.
- `docs/ai/PROMPT_TO_IMPLEMENTATION_CONTRACT.md`: traduccion de prompts informales a instrucciones precisas.
- `docs/adr/0001-agent-driven-change-control.md`: decision tecnica que formaliza el flujo.

Forma de uso:

1. Copiar los archivos respetando las rutas.
2. Revisar el reemplazo de `AGENTS.md`.
3. Commit separado, sin mezclar con cambios de codigo.
4. En futuros pedidos al agente, pedirle explicitamente que respete `AGENTS.md` y trabaje por slices.

Recomendacion: aplicar esto como un commit unico de documentacion/gobierno tecnico antes de seguir agregando features.
