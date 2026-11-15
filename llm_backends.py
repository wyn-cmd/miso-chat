# Model backends besides Gemini.
#
# Three kinds, all reached through the same small client shape rp_core already uses
# (chats.create / models.list / models.generate_content):
#
#   DeepSeek     OpenAI-style chat completions at https://api.deepseek.com (API key required)
#   Local LLM    any OpenAI-style server, e.g. vLLM or llama.cpp at http://host:port/v1 (key optional)
#   Claude Code  the local `claude` CLI in print mode, using the account it is signed in to
#   OpenRouter   OpenAI-style chat completions at https://openrouter.ai/api/v1 (API key required)
#
# Model ids carry a prefix: 'deepseek:deepseek-flash', 'local:<model>', 'claude:sonnet', 'openrouter:<id>'. A bare id
# goes to Gemini, so the existing fallback chain keeps working unchanged.
#
# Thinking is off by default. DeepSeek's chat endpoint is thinking-ON by default, so "off" is sent
# explicitly as {"thinking": {"type": "disabled"}}.
import json
import subprocess
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODELS = ("deepseek-flash", "deepseek-v4-pro")
CLAUDE_MODELS = ("sonnet", "opus", "haiku")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_LIST_TTL = 900     # seconds a fetched free-model list is reused
OPENROUTER_LIST_TIMEOUT = 10  # the model list is fetched while a page loads, so it gets a short timeout
TIMEOUT = 180
PREFIXES = {"deepseek:": "deepseek", "local:": "local", "claude:": "claude", "openrouter:": "openrouter",
            "opencode:": "opencode"}


class BackendError(RuntimeError):
    pass


class KeyRejected(BackendError):
    # One key of a pool was refused. until is when it may be tried again; the pool moves to the next key.
    def __init__(self, message, until):
        super().__init__(message)
        self.until = until


def _next_utc_midnight(now):
    return (int(now) // 86400 + 1) * 86400


def read_config(config):
    # (system_text, temperature, thinking_on, reasoning_level) from a genai-style GenerateContentConfig.
    system = getattr(config, "system_instruction", None) if config is not None else None
    if system is not None and not isinstance(system, str):
        system = "".join(getattr(p, "text", "") or "" for p in (getattr(system, "parts", None) or []))
    temperature = getattr(config, "temperature", None) if config is not None else None
    tc = getattr(config, "thinking_config", None) if config is not None else None
    thinking = bool(getattr(tc, "include_thoughts", False)) if tc is not None else False
    level = getattr(tc, "thinking_level", None) if tc is not None else None
    reasoning = str(getattr(level, "value", level) or "").lower()
    if reasoning not in ("low", "medium", "high"):
        reasoning = "low"
    return (system or None), temperature, thinking, reasoning


def contents_to_messages(contents):
    # genai Content list -> [{'role': 'user'|'assistant', 'content': text}].
    out = []
    for c in contents or []:
        role = "assistant" if getattr(c, "role", "user") == "model" else "user"
        text = "".join(getattr(p, "text", "") or "" for p in (getattr(c, "parts", None) or []))
        out.append({"role": role, "content": text})
    return out


def usage_obj(prompt=None, completion=None, total=None, cached=None):
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return SimpleNamespace(prompt_token_count=prompt, candidates_token_count=completion,
                           total_token_count=total, cached_content_token_count=cached)


def cached_from_usage(u):
    # Cached prompt tokens, from whichever field the provider uses. None when it reports nothing.
    if not isinstance(u, dict):
        return None
    if u.get("prompt_cache_hit_tokens") is not None:          # DeepSeek
        return u["prompt_cache_hit_tokens"]
    details = u.get("prompt_tokens_details")                  # OpenAI-style servers, e.g. vLLM
    if isinstance(details, dict):
        return details.get("cached_tokens")
    return None


class ThinkSplitter:
    # Splits inline <think>...</think> reasoning out of answer text. Local servers often inline it.
    # Handles tags split across stream chunks.
    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self):
        self.inside = False
        self.pending = ""

    def feed(self, piece):
        s = self.pending + piece
        self.pending = ""
        answer, thought = [], []
        while s:
            tag = self.CLOSE if self.inside else self.OPEN
            i = s.find(tag)
            if i >= 0:
                (thought if self.inside else answer).append(s[:i])
                s = s[i + len(tag):]
                self.inside = not self.inside
                continue
            keep = 0
            for k in range(min(len(tag) - 1, len(s)), 0, -1):
                if tag.startswith(s[-k:]):
                    keep = k
                    break
            body = s[:len(s) - keep]
            self.pending = s[len(s) - keep:]
            (thought if self.inside else answer).append(body)
            s = ""
        return "".join(answer), "".join(thought)

    def flush(self):
        rest, self.pending = self.pending, ""
        return ("", rest) if self.inside else (rest, "")


# ---------------------------------------------------------------- OpenAI-style (DeepSeek, local)

class OpenAIStyleClient:
    # Chat completions over HTTP. style 'deepseek' or 'local' decides how thinking is switched.

    def __init__(self, label, base_url, api_key, style):
        self.label = label
        self.base = (base_url or "").rstrip("/")
        # api_key may be one key or a list. A list is a pool for style 'openrouter': a key that hits its limit is
        # skipped until the limit resets, and the call is retried on the next key.
        keys = [api_key] if isinstance(api_key, str) else list(api_key or [])
        self.keys = [k for k in keys if k]
        self.api_key = self.keys[0] if self.keys else ""
        self._cool = {}               # key -> time it may be used again
        self._pool_lock = threading.Lock()
        self.style = style
        self.chats = _OpenAIChats(self)
        self._models = None           # (fetched_at, [ids]) for style 'openrouter'
        self._free_lock = threading.Lock()

    def headers(self, key=None):
        h = {"Content-Type": "application/json"}
        key = self.api_key if key is None else key
        if key:
            h["Authorization"] = "Bearer " + key
        return h

    def thinking_fields(self, enabled, reasoning="low"):
        if self.style == "deepseek":
            fields = {"thinking": {"type": "enabled" if enabled else "disabled"}}
            if enabled:
                fields["reasoning_effort"] = reasoning if reasoning in ("low", "medium", "high") else "low"
            return fields
        if self.style == "openrouter":
            # Sent only when thinking is on: many free models take no reasoning field at all.
            if not enabled:
                return {}
            return {"reasoning": {"effort": reasoning if reasoning in ("low", "medium", "high") else "low"}}
        return {"chat_template_kwargs": {"enable_thinking": bool(enabled)}}

    def open(self, path, body=None, method="POST", timeout=TIMEOUT, keyless=False):
        if self.style != "openrouter" or keyless:
            return self._open_once(path, body, method, timeout, "" if keyless else self.api_key)
        return self._open_pooled(path, body, method, timeout)

    # Tries each key that is not cooling down, in order. A key that is refused for its limit, or as bad, is parked and
    # the next one is tried. Only when every key is parked does the call fail.
    def _open_pooled(self, path, body, method, timeout):
        last = None
        tried = set()
        while True:
            with self._pool_lock:
                now = time.time()
                ready = [k for k in self.keys if k not in tried and self._cool.get(k, 0) <= now]
                key = ready[0] if ready else None
            if key is None:
                break
            tried.add(key)
            try:
                return self._open_once(path, body, method, timeout, key)
            except KeyRejected as err:
                last = err
                with self._pool_lock:
                    self._cool[key] = err.until
        if last is not None:
            raise BackendError(f"{self.label}: every key has reached its limit ({last})") from None
        raise BackendError(f"{self.label}: every key is cooling down after reaching its limit")

    def _open_once(self, path, body, method, timeout, key):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=self.headers(key), method=method)
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if self.style == "openrouter" and e.code in (401, 402, 403, 429):
                now = time.time()
                low = detail.lower()
                if e.code == 429:
                    # The daily free limit resets at midnight UTC. A per-minute limit clears within a minute.
                    daily = "per-day" in low or "per day" in low or "daily" in low
                    until = _next_utc_midnight(now) if daily else now + 60
                else:
                    until = now + 3600    # a bad key or one out of credit is retried in an hour
                raise KeyRejected(f"HTTP {e.code}: {detail[:120]}", until) from None
            raise BackendError(f"{self.label} answered HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, OSError) as e:
            raise BackendError(f"{self.label} is unreachable ({e.__class__.__name__})") from None

    # Every OpenRouter text model that costs nothing for both prompt and reply. The list is reused for
    # OPENROUTER_LIST_TTL seconds, and an older copy is kept when a refresh fails, so the picker survives an outage.
    def _openrouter_models(self):
        with self._free_lock:
            now = time.time()
            if self._models and now - self._models[0] < OPENROUTER_LIST_TTL:
                return list(self._models[1])
            # Prefer a real key when one is configured: OpenRouter's catalogue is public, but a key can expose
            # models (and pricing) that an anonymous request does not. Fall back to an anonymous request, then
            # to the last good list, if the key is missing or refused.
            data = None
            if self.keys:
                try:
                    with self.open("/models", method="GET", timeout=OPENROUTER_LIST_TIMEOUT) as r:
                        data = json.loads(r.read().decode("utf-8"))
                except (BackendError, ValueError):
                    data = None
            if data is None:
                try:
                    with self.open("/models", method="GET", timeout=OPENROUTER_LIST_TIMEOUT, keyless=True) as r:
                        data = json.loads(r.read().decode("utf-8"))
                except (BackendError, ValueError):
                    return list(self._models[1]) if self._models else []
            if not isinstance(data, dict):
                return list(self._models[1]) if self._models else []
            ids = []
            for m in data.get("data", []):
                if not isinstance(m, dict) or not m.get("id"):
                    continue
                out = (m.get("architecture") or {}).get("output_modalities")
                # Chat replies are text. A model that also makes audio or images (music and image models) is left out.
                if isinstance(out, list) and out and set(out) != {"text"}:
                    continue
                low = str(m.get("id") or "").lower()
                # Drop models that are not suited to roleplay: safety/guard models, speech models and the small
                # Liquid LFM models. Both their paid and free variants are skipped.
                if any(tag in low for tag in ("lfm", "guard", "safeguard", "moderation", "safety",
                                             "whisper", "tts", "audio", "embedding", "rerank", "fable")):
                    continue
                # Aion-Labs ships one dedicated roleplay model; keep that, drop the generic chat ones.
                if low.startswith("aion-labs/") and "aion-rp" not in low:
                    continue
                # Drop the Dots preview model.
                if "dots-3-note-preview" in low or "dots-studio" in low:
                    continue
                # Drop visual-reasoning and coding-reasoning models that are not for roleplay.
                if low.startswith("elorian/glimpse") or low.startswith("fireworks/ember-1"):
                    continue
                # Drop the older Gemma 2 family.
                if low.startswith("google/gemma-2-"):
                    continue
                # Drop IBM Granite 4.2 8B and the InclusionAI Ling family.
                if low.startswith("ibm-granite/granite-4.2-8b"):
                    continue
                if low.startswith("inclusionai/ling-"):
                    continue
                # Drop models that are not good for roleplay, by exact id (and any variant).
                blocked_exact = {
                    "kt/midm-k-3.0-pro", "mancer/weaver", "meituan/longcat-2.0",
                    "meta-llama/llama-3.1-70b-instruct", "meta-llama/llama-3.1-8b-instruct",
                    "meta-llama/llama-3.2-1b-instruct", "meta-llama/llama-3.2-3b-instruct",
                    "meta-llama/llama-3.3-70b-instruct", "meta-llama/llama-4-maverick",
                    "meta-llama/llama-4-scout", "inference-net/schematron-v2-turbo",
                    "inference-net/schematron-v2-small",
                    "meta/muse-ember", "meta/muse-glimmer-30b", "meta/muse-spark-1.1",
                    "meta/muse-spark-1.2", "meta/muse-spark-1.3", "meta/muse-spark-open",
                    "meta/muse-wonder", "microsoft/phi-4", "minimax/minimax-m2.5",
                    "minimax/minimax-m2.7", "minimax/minimax-m3", "minimax/minimax-m3.1-flash",
                    "minimax/minimax-m3:batch", "mistralai/codestral-2508", "mistralai/devstral-2512",
                    "moondream/moondream3-preview", "moondream/moondream3.1-9b-a2b",
                    "morph/morph-v3-fast", "morph/morph-v3-large", "motif-technologies/motif-3",
                    "mistralai/ministral-14b-2512", "mistralai/ministral-3b-2512", "mistralai/ministral-8b-2512",
                    "mistralai/mistral-large", "mistralai/mistral-large-2407", "mistralai/mistral-large-2512",
                    "mistralai/mistral-large-4-0", "mistralai/mistral-medium-3", "mistralai/mistral-medium-3-5",
                    "mistralai/mistral-medium-3-5:batch", "mistralai/mistral-medium-3.1", "mistralai/mistral-nemo",
                    "mistralai/mistral-saba", "mistralai/mistral-small-24b-instruct-2501",
                    "mistralai/mistral-small-2603", "mistralai/mistral-small-3.1-24b-instruct",
                    "mistralai/mistral-small-3.2-24b-instruct", "mistralai/mixtral-8x22b-instruct",
                    "mistralai/voxtral-small-24b-2507",
                    "nex-agi/nex-n2.5-mini", "nex-agi/nex-n2.5-pro", "nex-agi/nex-n2.6",
                    "nvidia/nemotron-3-nano-30b-a3b", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
                    "nvidia/nemotron-3.5-super-vl", "nvidia/switchyard",
                    "openrouter/bodybuilder", "openrouter/free", "openrouter/fusion", "openrouter/pareto-code",
                    "particle-ai/particle-1", "particle-ai/particle-1:free",
                    "perceptron/perceptron-mk1", "perceptron/perceptron-mk1.5",
                    "perplexity/sonar", "perplexity/sonar-deep-research", "perplexity/sonar-pro",
                    "perplexity/sonar-pro-search", "perplexity/sonar-reasoning-pro",
                    "prism-ml/ternary-bonsai-2-27b", "prism-ml/ternary-bonsai-2-9b",
                    "qwen/qwen-2.5-coder-32b-instruct", "qwen/qwen3-coder", "qwen/qwen3-coder-30b-a3b-instruct",
                    "qwen/qwen3-coder-flash", "qwen/qwen3-coder-next", "qwen/qwen2.5-vl-72b-instruct",
                    "qwen/qwen3-vl-235b-a22b-instruct", "qwen/qwen3-vl-30b-a3b-instruct",
                    "qwen/qwen3-vl-30b-a3b-thinking", "qwen/qwen3-vl-8b-instruct",
                    "qwen/qwen3.8-omni-flash",
                    "moonshotai/kimi-k2.7-code", "openai/gpt-5.1-codex", "openai/gpt-5.1-codex-max",
                    "openai/gpt-5.1-codex-mini", "openai/gpt-5.2-codex", "openai/gpt-5.3-codex",
                    "recursive/flint-v1.0", "reflection/beam-1.0-501b-a23b", "rekaai/reka-edge", "rekaai/reka-flash-3",
                    "relace/relace-apply-3", "relace/relace-search", "ricursiveai/ripple-65b-preview",
                    "sakana/fugu-max", "sakana/fugu-ultra", "sakana/fugu-ultra-v2", "scaledown/extract", "scaledown/summarize",
                    "stepfun/step-3.5-flash", "stepfun/step-3.7-flash", "stepfun/step-5-preview",
                    "tencent/hunyuan-a13b-instruct", "tencent/hy-mt2-1.8b", "tencent/hy-mt2-30b-a3b",
                    "tencent/hy-mt2-7b", "tencent/hy3", "tencent/hy3-preview", "tencent/hy4", "tencent/hy4-preview",
                    "thinkingmachines/inkling", "thinkingmachines/inkling-2-small", "thinkingmachines/inkling-2-small:free",
                    "thinkingmachines/inkling-small", "thinkingmachines/inkling-small:batch", "typesafe/jev-router",
                    "unbiased/pareto-26.10", "unbiased/pareto-26.10-preview", "unbiased/pareto-26.9",
                    "upstage/solar-mini4", "writer/palmyra-x5",
                    "z-ai/glm-4.5v", "z-ai/glm-4.6v", "z-ai/glm-5v-turbo", "z-ai/glm-5.3-flash:batch",
                    "z-ai/glm-5.3:batch",
                }
                if low.split(":")[0] in blocked_exact:
                    continue
                # Drop the code-focused Cohere model and batch-only variants, which are not for live chat.
                if "north-mini-code" in low:
                    continue
                if low.endswith(":batch"):
                    continue
                # Amazon models are not kept.
                if low.startswith("amazon/"):
                    continue
                # Baidu and ByteDance models are not kept.
                if low.startswith("baidu/") or low.startswith("bytedance-seed/") or low.startswith("bytedance/"):
                    continue
                # Older Gemini 2.x models are not kept.
                if low.startswith("google/gemini-2."):
                    continue
                # Gemini preview models are not kept.
                if low.startswith("google/gemini-") and "preview" in low:
                    continue
                # Anthropic: only newer models (5.x and up) are kept.
                if low.startswith("anthropic/"):
                    import re as _re
                    mm = _re.search(r"claude-(?:\w+-)?(\d+(?:\.\d+)?)", low)
                    ver = float(mm.group(1)) if mm else 0.0
                    if ver < 5.0:
                        continue
                ids.append(m["id"])
            ids.sort()
            self._models = (now, ids)
            return list(ids)

    def model_names(self):
        if self.style == "deepseek":
            return list(DEEPSEEK_MODELS)
        if self.style == "openrouter":
            return self._openrouter_models()
        try:
            with self.open("/models", method="GET") as r:
                data = json.loads(r.read().decode("utf-8"))
            return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
        except BackendError:
            return []


def _is_zero_price(value):
    # OpenRouter sends prices as strings such as "0" or "0.0000015"; a missing price is not treated as free.
    try:
        return value is not None and float(value) == 0.0
    except (TypeError, ValueError):
        return False


class _OpenAIChats:
    def __init__(self, client):
        self.client = client

    def create(self, model=None, history=None, config=None):
        return OpenAIStyleChat(self.client, model, history, config)


class OpenAIStyleChat:
    def __init__(self, client, model, history, config):
        self.client = client
        self.model = model
        self.system, self.temperature, self.thinking, self.reasoning = read_config(config)
        self.history = contents_to_messages(history)

    def _body(self, text, stream):
        msgs = ([{"role": "system", "content": self.system}] if self.system else [])
        msgs += self.history + [{"role": "user", "content": text}]
        body = {"model": self.model, "messages": msgs, "stream": stream}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        body.update(self.client.thinking_fields(self.thinking, self.reasoning))
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    def _remember(self, user_text, answer):
        self.history.append({"role": "user", "content": user_text})
        self.history.append({"role": "assistant", "content": answer})

    def send_message(self, text):
        with self.client.open("/chat/completions", self._body(text, False)) as r:
            data = json.loads(r.read().decode("utf-8"))
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        answer = msg.get("content") or ""
        thought = msg.get("reasoning_content") or msg.get("reasoning") or ""
        if self.client.style in ("local", "openrouter") and "<think>" in answer:
            sp = ThinkSplitter()
            a, t = sp.feed(answer)
            a2, t2 = sp.flush()
            answer, thought = a + a2, t + t2
        self._remember(text, answer)
        u = data.get("usage") or {}
        return SimpleNamespace(text=answer or None, thought=(thought if self.thinking else ""),
                               usage_metadata=usage_obj(u.get("prompt_tokens"), u.get("completion_tokens"),
                                                        u.get("total_tokens"), cached_from_usage(u)))

    def send_message_stream(self, text):
        use_tags = self.client.style in ("local", "openrouter")
        sp = ThinkSplitter()
        answer_parts, usage = [], None
        with self.client.open("/chat/completions", self._body(text, True)) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    event = json.loads(payload)
                except ValueError:
                    continue
                if event.get("usage"):
                    u = event["usage"]
                    usage = usage_obj(u.get("prompt_tokens"), u.get("completion_tokens"), u.get("total_tokens"),
                                      cached_from_usage(u))
                choices = event.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                answer_piece = delta.get("content") or ""
                thought_piece = delta.get("reasoning_content") or delta.get("reasoning") or ""
                if use_tags and answer_piece:
                    answer_piece, inline = sp.feed(answer_piece)
                    thought_piece += inline
                if answer_piece:
                    answer_parts.append(answer_piece)
                if thought_piece and self.thinking:
                    yield SimpleNamespace(text=None, thought=thought_piece, usage_metadata=None)
                if answer_piece:
                    yield SimpleNamespace(text=answer_piece, thought=None, usage_metadata=None)
        if use_tags:
            tail_answer, tail_thought = sp.flush()
            if tail_answer:
                answer_parts.append(tail_answer)
                yield SimpleNamespace(text=tail_answer, thought=None, usage_metadata=None)
            if tail_thought and self.thinking:
                yield SimpleNamespace(text=None, thought=tail_thought, usage_metadata=None)
        self._remember(text, "".join(answer_parts))
        yield SimpleNamespace(text=None, thought=None, usage_metadata=usage)


# ---------------------------------------------------------------- Claude Code (print mode)

class ClaudeCodeClient:
    # Runs the local `claude` CLI once per message, with no tools. workdir must be an empty folder:
    # Claude Code loads project instruction files from its working directory.
    label = "claude"
    style = "claude"

    def __init__(self, exe, workdir):
        self.exe = exe
        self.workdir = workdir
        self.chats = _ClaudeChats(self)

    def model_names(self):
        return list(CLAUDE_MODELS)


class _ClaudeChats:
    def __init__(self, client):
        self.client = client

    def create(self, model=None, history=None, config=None):
        return ClaudeCodeChat(self.client, model, history, config)


class ClaudeCodeChat:
    def __init__(self, client, model, history, config):
        self.client = client
        self.model = model
        self.system, self.temperature, self.thinking, self.reasoning = read_config(config)
        self.history = contents_to_messages(history)

    def _prompt(self, text):
        parts = []
        if self.system:
            parts.append("Instructions for this roleplay. Follow them, and reply only in character, "
                         "with no preamble or commentary:\n" + self.system)
        if self.history:
            convo = "\n\n".join(("User: " if m["role"] == "user" else "Assistant: ") + m["content"]
                                for m in self.history)
            parts.append("Earlier conversation:\n" + convo)
        parts.append("Reply to the latest message below.\n\nUser: " + text)
        return "\n\n".join(parts)

    def _run(self, text):
        # system text goes in a file (line breaks in command-line arguments break through claude.cmd).
        # --setting-sources "" keeps the personal project instructions and settings out of roleplay replies.
        import os
        import tempfile
        fd, sys_path = tempfile.mkstemp(suffix=".txt", prefix="rp_sys_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(self.system or "You are a roleplay character. Stay in character and reply in character.")
        cmd = [self.client.exe, "-p", "--model", self.model, "--tools", "", "--no-session-persistence",
               "--setting-sources", "", "--system-prompt-file", sys_path,
               "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=self.client.workdir, text=True, encoding="utf-8", errors="replace")
            proc.stdin.write(self._prompt(text))
            proc.stdin.close()
            try:
                for line in proc.stdout:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue
            finally:
                rc = proc.wait(timeout=60)
                err = (proc.stderr.read() or "").strip()
                if rc != 0:
                    raise BackendError(f"claude exited with code {rc}: {err[-300:]}")
        finally:
            try:
                os.remove(sys_path)
            except OSError:
                pass


    def send_message(self, text):
        answer, usage = "", None
        for ev in self._run(text):
            if ev.get("type") == "result":
                answer = ev.get("result") or answer
                usage = ev.get("usage") or usage
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": answer}]
        return SimpleNamespace(text=answer or None, thought="", usage_metadata=_claude_usage(usage))

    def send_message_stream(self, text):
        parts, usage = [], None
        for ev in self._run(text):
            if ev.get("type") == "stream_event":
                event = ev.get("event") or {}
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        parts.append(delta["text"])
                        yield SimpleNamespace(text=delta["text"], thought=None, usage_metadata=None)
                    elif delta.get("type") == "thinking_delta" and delta.get("thinking") and self.thinking:
                        yield SimpleNamespace(text=None, thought=delta["thinking"], usage_metadata=None)
            elif ev.get("type") == "result":
                usage = ev.get("usage") or usage
                if not parts and ev.get("result"):
                    parts.append(ev["result"])
                    yield SimpleNamespace(text=ev["result"], thought=None, usage_metadata=None)
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": "".join(parts)}]
        yield SimpleNamespace(text=None, thought=None, usage_metadata=_claude_usage(usage))


def _claude_usage(usage):
    # Anthropic reports cache reads and writes apart from input_tokens; the prompt total adds them back.
    if not isinstance(usage, dict):
        return None
    read = usage.get("cache_read_input_tokens") or 0
    write = usage.get("cache_creation_input_tokens") or 0
    prompt = (usage.get("input_tokens") or 0) + read + write
    return usage_obj(prompt, usage.get("output_tokens"), None, read)


# ---------------------------------------------------------------- usage of DeepSeek calls

# What one cached DeepSeek prompt token counts for against the token budget, against 1 for every other token.
CACHED_TOKEN_BUDGET_WEIGHT = 0.05

def with_usage(client, sink):
    # A view of client that reports every DeepSeek call to sink(model, figures, seconds), as the call returns.
    # figures holds input_tokens, cached_tokens, output_tokens and tokens. Other models pass through unchanged,
    # so each call site uses the view exactly like the client, and no call site has to count its own tokens.
    return UsageClient(client, sink)


class UsageClient:
    def __init__(self, client, sink):
        self._client = client
        self._sink = sink
        self.chats = _UsageChats(self)

    def __getattr__(self, name):
        return getattr(self._client, name)


class _UsageChats:
    def __init__(self, view):
        self.view = view

    def create(self, **kwargs):
        chat = self.view._client.chats.create(**kwargs)
        model = kwargs.get("model") or ""
        if model.startswith("deepseek:"):
            return _UsageChat(chat, self.view._sink, model)
        return chat


class _UsageChat:
    def __init__(self, chat, sink, model):
        self._chat = chat
        self._sink = sink
        self._model = model

    def __getattr__(self, name):
        return getattr(self._chat, name)

    def send_message(self, *args, **kwargs):
        start = time.time()
        resp = self._chat.send_message(*args, **kwargs)
        self._report(getattr(resp, "usage_metadata", None), time.time() - start)
        return resp

    def send_message_stream(self, *args, **kwargs):
        # The usage arrives on the last chunk the provider sends, so the figures are read as the chunks pass.
        start = time.time()
        usage = None
        for chunk in self._chat.send_message_stream(*args, **kwargs):
            found = getattr(chunk, "usage_metadata", None)
            if found is not None:
                usage = found
            yield chunk
        self._report(usage, time.time() - start)

    def _report(self, usage, seconds):
        # A call that reports nothing is not counted. Cached tokens are part of the prompt, never more than it.
        if usage is None:
            return
        prompt = int(getattr(usage, "prompt_token_count", None) or 0)
        cached = min(int(getattr(usage, "cached_content_token_count", None) or 0), prompt)
        output = int(getattr(usage, "candidates_token_count", None) or 0)
        total = int(getattr(usage, "total_token_count", None) or 0) or prompt + output
        if not (prompt or output or total):
            return
        # The token budget counts a cached prompt token at a tenth of a new one. The input, cached and output columns
        # keep the raw counts, so the stats page still shows what was sent.
        budget_tokens = round((prompt - cached) + cached * CACHED_TOKEN_BUDGET_WEIGHT + output)
        self._sink(self._model, {"input_tokens": prompt, "cached_tokens": cached,
                                 "output_tokens": output, "tokens": budget_tokens}, round(seconds, 3))


# ---------------------------------------------------------------- Opencode (local CLI, free models)
class OpencodeClient:
    label = "opencode"
    style = "opencode"
    OPENCODE_LIST_TTL = 900
    OPENCODE_EXHAUSTED_TTL = 900

    def __init__(self, exe="opencode"):
        self.exe = exe or "opencode"
        self.chats = _OpencodeChats(self)
        self._models = None
        self._models_at = 0.0
        self._models_lock = threading.Lock()
        self._exhausted = {}   # model -> time it may be tried again

    def _run_models(self):
        ids = []
        try:
            proc = subprocess.run([self.exe, "models"], capture_output=True, text=True, timeout=30, errors="replace")
            for line in (proc.stdout or "").splitlines():
                line = line.strip()
                if line.startswith("opencode/"):
                    ids.append(line)
        except (OSError, subprocess.SubprocessError):
            pass
        return sorted(set(ids))

    def _raw_models(self):
        now = time.time()
        if self._models is not None and now - self._models_at < self.OPENCODE_LIST_TTL:
            return list(self._models)
        ids = self._run_models()
        self._models = ids
        self._models_at = now
        return list(ids)

    def model_names(self):
        with self._models_lock:
            now = time.time()
            ids = self._raw_models()
            return [m for m in ids if self._exhausted.get(m, 0) <= now]

    def mark_exhausted(self, model):
        if model:
            with self._models_lock:
                self._exhausted[model] = time.time() + self.OPENCODE_EXHAUSTED_TTL

    def probe_models(self):
        # A tiny request against every model. Returns {id: True/False}; failures are marked
        # exhausted so they leave the picker until the cooldown passes.
        with self._models_lock:
            ids = self._raw_models()
        out = {}
        for mid in ids:
            ok = False
            try:
                proc = subprocess.run([self.exe, "run", "-m", mid, "--format", "json", "ping"],
                                      capture_output=True, text=True, timeout=60, errors="replace")
                ok = proc.returncode == 0 and "text" in (proc.stdout or "")
            except (OSError, subprocess.SubprocessError):
                ok = False
            out[mid] = ok
            if ok:
                with self._models_lock:
                    self._exhausted.pop(mid, None)
            else:
                self.mark_exhausted(mid)
        return out


class _OpencodeChats:
    def __init__(self, client):
        self.client = client

    def create(self, model=None, history=None, config=None):
        return OpencodeChat(self.client, model, history, config)


class OpencodeChat:
    def __init__(self, client, model, history, config):
        self.client = client
        self.model = model or "opencode/big-pickle"
        self.system, self.temperature, self.thinking, self.reasoning = read_config(config)
        self.history = contents_to_messages(history)

    def _prompt(self, text):
        parts = []
        if self.system:
            parts.append(self.system)
        for m in self.history:
            parts.append(("User: " if m["role"] == "user" else "Assistant: ") + m["content"])
        parts.append("User: " + text)
        return "\n\n".join(parts)

    def send_message(self, text):
        prompt = self._prompt(text)
        try:
            proc = subprocess.run([self.client.exe, "run", "-m", self.model, "--format", "json", prompt],
                                  capture_output=True, text=True, timeout=300, errors="replace")
        except (OSError, subprocess.SubprocessError) as err:
            raise BackendError(f"opencode is unreachable ({err.__class__.__name__})") from None
        answer = ""
        for line in (proc.stdout or "").splitlines():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "text":
                answer += (ev.get("part") or {}).get("text") or ""
        if not answer and proc.returncode != 0:
            detail = ((proc.stderr or "") + "\n" + (proc.stdout or "")).lower()
            # A quota or rate-limit failure removes this model from the picker until it refreshes.
            if any(tag in detail for tag in ("quota", "rate limit", "ratelimit", "429", "exceeded",
                                             "no credits", "unavailable", "capacity")):
                self.client.mark_exhausted(self.model)
            raise BackendError(f"opencode exited with code {proc.returncode}: {(proc.stderr or '')[-200:]}")
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": answer}]
        return SimpleNamespace(text=answer or None, thought="", usage_metadata=None)

    def send_message_stream(self, text):
        resp = self.send_message(text)
        if resp.text:
            yield SimpleNamespace(text=resp.text, thought=None, usage_metadata=None)
        yield SimpleNamespace(text=None, thought=None, usage_metadata=None)


# ---------------------------------------------------------------- routing

class MultiClient:
    # Routes by model id prefix. Bare ids go to Gemini, the google-genai client.
    #
    # providers maps 'deepseek' / 'local' / 'claude' / 'openrouter' to a client, or to None when that backend is off.

    def __init__(self, gemini, providers):
        self.gemini = gemini
        self.providers = providers
        self.chats = _MultiChats(self)
        self.models = _MultiModels(self)

    def route(self, model):
        for prefix, name in PREFIXES.items():
            if (model or "").startswith(prefix):
                client = self.providers.get(name)
                if client is None:
                    raise BackendError(f"{name} is not set up on this server")
                return client, model[len(prefix):]
        return self.gemini, model


class _MultiChats:
    def __init__(self, multi):
        self.multi = multi

    def create(self, model=None, history=None, config=None):
        client, bare = self.multi.route(model)
        return client.chats.create(model=bare, history=history, config=config)


class _MultiModels:
    def __init__(self, multi):
        self.multi = multi

    def list(self):
        out = list(self.multi.gemini.models.list())
        for name, client in self.multi.providers.items():
            if client is None:
                continue
            prefix = next(p for p, n in PREFIXES.items() if n == name)
            out.extend(SimpleNamespace(name=prefix + m) for m in client.model_names())
        return out
