# Integración de NVIDIA NIM — DeepSeek-V4-Pro

## Por qué se añade este soporte

La cuenta de prueba gratuita de Google Gemini ha expirado. Como alternativa sin coste inmediato, NVIDIA ofrece créditos de API a través de su plataforma NIM (NVIDIA Inference Microservices), que expone modelos — entre ellos `deepseek-ai/deepseek-v4-pro` — mediante una API compatible con OpenAI.

Esto también es una buena oportunidad para documentar **lo sencillo que es cambiar de proveedor LLM** en esta librería gracias a su arquitectura en capas.

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

El paso de Jenkins (`FixWithAI.groovy`) ya gestiona esto automáticamente: detecta el prefijo del modelo (`gemini/`, `anthropic/`, `ollama/`…) y exporta la variable de credencial correcta.

---

## Qué hace falta para usar NVIDIA / DeepSeek

### Cómo funciona litellm con endpoints OpenAI-compatibles

Para redirigir litellm a cualquier API compatible con OpenAI basta con:

```
LLM_MODEL     = openai/deepseek-ai/deepseek-v4-pro
OPENAI_API_KEY = <nvidia-api-key>
OPENAI_API_BASE = https://integrate.api.nvidia.com/v1
```

El prefijo `openai/` le dice a litellm que use el protocolo OpenAI; `OPENAI_API_BASE` sustituye la URL base por la de NVIDIA. **El código Python no necesita ningún cambio.**

---

### Ejecución local (sin Jenkins)

Si se lanza el agente directamente desde CLI, funciona sin tocar ningún fichero:

```bash
export LLM_MODEL="openai/deepseek-ai/deepseek-v4-pro"
export OPENAI_API_KEY="<nvidia-api-key>"
export OPENAI_API_BASE="https://integrate.api.nvidia.com/v1"

python3 .ai_fixer/mcp_agent.py --repo <repo> --model openai/deepseek-ai/deepseek-v4-pro ...
```

---

### Ejecución vía Jenkins (`FixWithAI.groovy`) — cambios necesarios

El Groovy es el único sitio que inyecta variables de entorno en el proceso Python. Actualmente **no exporta `OPENAI_API_BASE`**, por lo que litellm seguiría apuntando a `api.openai.com` aunque el modelo sea correcto.

#### Cambio 1 — Añadir el parámetro `llmBaseUrl`

En la sección de parámetros de `FixWithAI.groovy` (donde están `llmModel`, `llmCredentialId`, etc.) añadir:

```groovy
String llmBaseUrl = ''          // URL base del endpoint (vacío = proveedor por defecto)
```

#### Cambio 2 — Detectar el proveedor NVIDIA en la selección de credencial

En el bloque que asigna `envKeyName` (líneas ~167-179), añadir un caso para NVIDIA:

```groovy
} else if (llmModel.startsWith('nvidia/')) {
    envKeyName = 'OPENAI_API_KEY'     // NVIDIA usa el mismo nombre de variable
    resolvedModel = "openai/${llmModel.replaceFirst('nvidia/', '')}"
}
```

O, si se prefiere pasar el modelo ya con el prefijo `openai/deepseek-ai/...` directamente, no hace falta esta rama — el caso por defecto (`OPENAI_API_KEY`) ya es correcto.

#### Cambio 3 — Exportar `OPENAI_API_BASE` si se ha proporcionado

En el bloque `sh """..."""` que lanza el agente Python (líneas ~250-267), añadir después del export del modelo:

```groovy
${llmBaseUrl ? "export OPENAI_API_BASE=${shellQuote(llmBaseUrl)}" : ''}
```

Quedaría así dentro del bloque shell:

```groovy
export LLM_MODEL=${shellQuote(resolvedModel)}
export ${envKeyName}="\${LLM_API_KEY_VALUE}"
${llmBaseUrl ? "export OPENAI_API_BASE=${shellQuote(llmBaseUrl)}" : ''}
...
```

---

### Uso en el Jenkinsfile tras el cambio

```groovy
FixWithAI(
    llmModel:        'openai/deepseek-ai/deepseek-v4-pro',
    llmCredentialId: 'Nvidia_Api_Token',   // credencial Jenkins con la API key de NVIDIA
    llmBaseUrl:      'https://integrate.api.nvidia.com/v1',
    ...
)
```

---

## Nota sobre el parámetro `thinking`

El código de ejemplo de NVIDIA pasa `extra_body={"chat_template_kwargs":{"thinking":False}}` para desactivar la cadena de razonamiento interna del modelo. La librería no transmite ese parámetro.

Sin él, DeepSeek puede devolver bloques `<think>...</think>` en sus respuestas. El agente seguirá funcionando correctamente — litellm filtra o pasa el contenido tal cual — pero si se quisiera suprimir ese output, habría que añadir `extra_body` al `litellm.completion()` de `agent_loop.py`.

---

## Resumen de cambios

| Fichero | ¿Cambio necesario? | Qué cambiar |
|---------|--------------------|-------------|
| `agent_loop.py` | **No** | — |
| `entrypoint.py` | **No** | — |
| `env_config.py` | **No** | — |
| `FixWithAI.groovy` | **Sí** | Parámetro `llmBaseUrl` + export `OPENAI_API_BASE` |
| `agent_loop.py` (opcional) | Opcional | Añadir `extra_body` para suprimir thinking tokens |
