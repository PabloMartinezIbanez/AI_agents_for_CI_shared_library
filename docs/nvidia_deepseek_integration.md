# Integración de NVIDIA NIM — DeepSeek-V4-Pro

## Por qué se añade este soporte

La cuenta de prueba gratuita de Google Gemini expiró. Como alternativa sin coste inmediato, NVIDIA ofrece créditos de API a través de su plataforma NIM (NVIDIA Inference Microservices), que expone modelos — entre ellos `deepseek-ai/deepseek-v4-pro` — mediante una API compatible con OpenAI.

Esto también sirve para demostrar **lo sencillo que es cambiar de proveedor LLM** en esta librería gracias a su arquitectura en capas.

---

## Conclusión tras la prueba

**DeepSeek-V4-Pro a través de NVIDIA NIM funciona correctamente**, pero es significativamente más lento que Gemini para el mismo trabajo: cada iteración del agente tarda considerablemente más, lo que hace que una ejecución completa se extienda mucho más de lo aceptable en un pipeline de CI.

Por este motivo se ha decidido **volver a usar la API de Gemini en su plan de pago**, aceptando el coste a cambio de la velocidad necesaria para que el agente sea práctico.

El soporte para NVIDIA queda implementado en el código por si se quisiera usar en el futuro.

---

## Por qué es fácil cambiar de modelo en esta librería

El código Python que ejecuta el agente no conoce ni se preocupa de qué modelo hay detrás. Toda la lógica — el bucle de iteraciones, la test gate, la llamada a herramientas MCP — pasa por una única línea en `agent_loop.py`:

```python
response = litellm.completion(
    model=model,   # ← viene de LLM_MODEL, o --model en CLI
    messages=messages,
    ...
)
```

[litellm](https://github.com/BerriAI/litellm) es una capa de abstracción que traduce esa llamada al formato correcto para cada proveedor (OpenAI, Gemini, Anthropic, Ollama, y cualquier endpoint compatible con OpenAI). El resto del sistema — herramientas, prompts, test gate, reportes — no cambia en absoluto.

Para cambiar de modelo basta con dos variables de entorno:

| Variable | Efecto |
|----------|--------|
| `LLM_MODEL` | Qué modelo usar (en formato litellm, p.ej. `gemini/gemini-2.0-flash`) |
| `<PROVEEDOR>_API_KEY` | Credencial para ese proveedor |

El paso de Jenkins (`FixWithAI.groovy`) gestiona esto automáticamente: detecta el prefijo del modelo (`gemini/`, `anthropic/`, `nvidia/`…) y exporta la variable de credencial y la URL base correctas.

---

## Cambios implementados para NVIDIA / DeepSeek

### `FixWithAI.groovy`

Se añadió una rama en el detector de proveedores. Cuando `llmModel` empieza por `nvidia/`, el Groovy:
1. Mantiene `OPENAI_API_KEY` como nombre de variable de credencial (NVIDIA usa el mismo protocolo que OpenAI).
2. Transforma el modelo a formato litellm: `nvidia/deepseek-ai/deepseek-v4-pro` → `openai/deepseek-ai/deepseek-v4-pro`.
3. Exporta `AI_BASE_URL=https://integrate.api.nvidia.com/v1` automáticamente — sin que el Jenkinsfile tenga que especificarlo.

```groovy
} else if (llmModel.startsWith('nvidia/')) {
    envKeyName = 'OPENAI_API_KEY'
    resolvedModel = "openai/${llmModel.replaceFirst('nvidia/', '')}"
    llmBaseUrl = 'https://integrate.api.nvidia.com/v1'
}
```

### `entrypoint.py`

- Lee `AI_BASE_URL` del entorno y lo pasa a `run_agent_loop`.
- Si `AI_BASE_URL` está configurada y el modelo no tiene prefijo de proveedor conocido, añade `openai/` automáticamente (cubre el caso de pasar el modelo sin prefijo desde Jenkins o CLI).
- Si la URL contiene `"nvidia"`, activa `extra_body={"thinking": {"type": "disabled"}}` para desactivar la cadena de razonamiento interna de DeepSeek (ver sección siguiente).
- Incluye `Base URL` en el log de arranque cuando está configurada.

### `agent_loop.py`

- Añadidos los parámetros `base_url` y `extra_body` a `run_agent_loop`.
- Ambos se pasan directamente a `litellm.completion()`.

### Uso en el Jenkinsfile

```groovy
FixWithAI(
    llmModel:        'nvidia/deepseek-ai/deepseek-v4-pro',
    llmCredentialId: 'Nvidia_Api_Token',
    ...
)
```

No se necesita ningún parámetro adicional; la URL base y el `extra_body` se gestionan internamente.

---

## El problema del "thinking" y por qué ralentiza el agente

DeepSeek-V4-Pro es un modelo de razonamiento que por defecto genera una cadena interna de pensamiento (`<think>...</think>`) antes de cada respuesta. Esto puede producir miles de tokens adicionales por iteración — tokens que el agente no necesita y que alargan cada llamada al LLM considerablemente.

La librería desactiva este comportamiento automáticamente cuando detecta NVIDIA como proveedor:

```python
extra_body = {"thinking": {"type": "disabled"}} if base_url and "nvidia" in base_url else None
```

Incluso con el thinking desactivado, DeepSeek-V4-Pro demostró ser notablemente más lento que `gemini-2.0-flash` para el volumen de trabajo típico del agente.

---

## Resumen de cambios en el código

| Fichero | Cambio |
|---------|--------|
| `FixWithAI.groovy` | Rama `nvidia/` en el detector de proveedores; `AI_BASE_URL` hardcodeada para ese proveedor |
| `entrypoint.py` | Lee `AI_BASE_URL`; normaliza prefijo del modelo; construye `extra_body`; pasa ambos a `run_agent_loop` |
| `agent_loop.py` | Parámetros `base_url` y `extra_body` en `run_agent_loop`; ambos pasados a `litellm.completion()` |
