# Models

Fox works with any chat model that can call tools. This page covers the built-in providers, adding your own models, choosing a model per chat, and which models handle images.

- [How Fox picks a model](#how-fox-picks-a-model)
- [Built-in providers](#built-in-providers)
- [Adding a model](#adding-a-model)
- [Choosing a model for one chat](#choosing-a-model-for-one-chat)
- [Images and documents](#images-and-documents)
- [Agents](#agents)
- [Testing and troubleshooting](#testing-and-troubleshooting)

## How Fox picks a model

Fox tries models in the **fallback order** (`FOX_MODEL_CHAIN`), left to right. A model with no key is skipped. A model that fails or times out is skipped for `FOX_MODEL_COOLDOWN` seconds, and Fox moves to the next one, so one provider being down doesn't stop you.

Each entry in the order can be:

| Entry | Meaning |
|---|---|
| `gemini` | A built-in provider with its default model (`GEMINI_MODEL`). |
| `groq:llama-3.3-70b-versatile` | A built-in provider with a specific model. |
| `deepseek` | The name of a model you added on the **Models** page. |

```ini
FOX_MODEL_CHAIN=deepseek,gemini,groq:llama-3.3-70b-versatile,ollama
```

## Built-in providers

Azure OpenAI, OpenAI, Anthropic, Google Gemini, Groq and Ollama work with nothing more than a key in `.env`, or in the app under **Models → Provider keys**. The settings for each are in [Configuration](configuration.md#providers).

## Adding a model

Open **Models** and click **Add model**.

| Field | What to enter |
|---|---|
| **Name** | What you'll call it, such as `deepseek`. Lowercase letters, numbers, `.`, `-` and `_`. It can't be a provider name. |
| **Provider** | One of the built-in providers, or **OpenAI-compatible server** for anything that speaks the OpenAI API. |
| **Model id** | The model's id as the provider names it. |
| **Server address** | Required for an OpenAI-compatible server. Optional for the others, which use their usual address. |
| **API key** | Optional for built-in providers: without one, the model uses that provider's key from `.env`. |
| **It can read images** | Tick it for vision models. They go first when a message has an image. |

Click **Save model**, then **Test**. Fox sends a one-word question and shows the answer and how long it took. To use the model, add its name to the fallback order (on the same page), or pick it from the **Model** menu in chat.

Examples:

| Name | Provider | Model id | Server address |
|---|---|---|---|
| `deepseek` | OpenAI-compatible | `deepseek-chat` | `https://api.deepseek.com/v1` |
| `openrouter` | OpenAI-compatible | any model OpenRouter lists | `https://openrouter.ai/api/v1` |
| `lmstudio` | OpenAI-compatible | the model loaded in LM Studio | `http://localhost:1234/v1` (no key) |
| `gpt-mini` | OpenAI | `gpt-4.1-mini` | (empty) |
| `gemini-pro` | Google Gemini | `gemini-2.5-pro` | (empty) |
| `vision` | Ollama | `llama3.2-vision` | (empty), with **It can read images** ticked |

Models you add are stored in `data/models.json`, keys included. Tools can't read the `data` folder, and the app never sends a saved key back to the page; it only shows whether one is set. Treat the file like `.env`.

## Choosing a model for one chat

The **Model** menu under the message box picks who answers. **Auto** follows the fallback order. Any other choice is used for every message until you change it, with no fallback: if that model fails, you see the error. The choice is remembered between sessions.

## Images and documents

Documents you attach (PDF, Word, text, code, CSV and so on) are turned into text, so every model can read them.

Images need a model that can see. When a message, or an earlier message in the chat, has an image, Fox uses a different order:

1. `FOX_VISION_CHAIN`, if you set it, such as `FOX_VISION_CHAIN=gemini,openai:gpt-4o`.
2. Otherwise, models you added with **It can read images** ticked, then the normal fallback order.

A model that can't read images usually refuses the request, and Fox moves to the next one. Most current Gemini, OpenAI, Anthropic and Azure OpenAI models can read images. Groq and Ollama depend on the model.

## Agents

An agent's `models` list takes the same entries as the fallback order, so a specialist can use a model you added:

```yaml
models: [deepseek, groq]
```

## Testing and troubleshooting

- **Models → In use now** shows the fallback order as Fox resolves it, skipping models with no key, with a **Test** button for every model that's ready.
- `fox doctor` lists the providers, the models you added, and the resolved fallback order.
- A model marked **Needs a key or address** is skipped everywhere. Add its key, or the provider's key under **Provider keys**.
- The model must support **tool calling** for Fox to act. Small local models often call tools poorly; see [Limitations](limitations.md#models).
