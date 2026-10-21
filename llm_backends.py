# Model backends besides Gemini.
#
# Three kinds, all reached through the same small client shape rp_core already uses
# (chats.create / models.list / models.generate_content):
#
#   DeepSeek     OpenAI-style chat completions at https://api.deepseek.com (API key required)
#   Local LLM    any OpenAI-style server, e.g. vLLM or llama.cpp at http://host:port/v1 (key optional)
#   Claude Code  the local `claude` CLI in print mode, using the account it is signed in to
#
# Model ids carry a prefix: 'deepseek:deepseek-flash', 'local:<model>', 'claude:sonnet'. A bare id
# goes to Gemini, so the existing fallback chain keeps working unchanged.
#
# Thinking is off by default. DeepSeek's chat endpoint is thinking-ON by default, so "off" is sent
# explicitly as {"thinking": {"type": "disabled"}}.
import json
import subprocess
from types import SimpleNamespace
import urllib.error
import urllib.request

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODELS = ("deepseek-flash", "deepseek-v4-pro")
CLAUDE_MODELS = ("sonnet", "opus", "haiku")
TIMEOUT = 180
PREFIXES = {"deepseek:": "deepseek", "local:": "local", "claude:": "claude"}


class BackendError(RuntimeError):
    pass


def read_config(config):
    # (system_text, temperature, thinking_on) from a genai-style GenerateContentConfig, or defaults.
    system = getattr(config, "system_instruction", None) if config is not None else None
    if system is not None and not isinstance(system, str):
        system = "".join(getattr(p, "text", "") or "" for p in (getattr(system, "parts", None) or []))
    temperature = getattr(config, "temperature", None) if config is not None else None
    tc = getattr(config, "thinking_config", None) if config is not None else None
    thinking = bool(getattr(tc, "include_thoughts", False)) if tc is not None else False
    return (system or None), temperature, thinking


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


def _models_adapter(client):
    return SimpleNamespace(
        list=lambda: [SimpleNamespace(name=m) for m in client.model_names()],
        generate_content=lambda model=None, contents="", **_: _one_shot(client, model, contents),
    )


def _one_shot(client, model, contents):
    if not isinstance(contents, str):
        contents = "\n".join(getattr(p, "text", "") or "" for c in contents for p in (getattr(c, "parts", None) or []))
    return client.chats.create(model=model, history=None, config=None).send_message(contents)


# ---------------------------------------------------------------- OpenAI-style (DeepSeek, local)

class OpenAIStyleClient:
    # Chat completions over HTTP. style 'deepseek' or 'local' decides how thinking is switched.

    def __init__(self, label, base_url, api_key, style):
        self.label = label
        self.base = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.style = style
        self.chats = _OpenAIChats(self)
        self.models = _models_adapter(self)

    def headers(self):
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = "Bearer " + self.api_key
        return h

    def thinking_fields(self, enabled):
        if self.style == "deepseek":
            fields = {"thinking": {"type": "enabled" if enabled else "disabled"}}
            if enabled:
                fields["reasoning_effort"] = "high"
            return fields
        return {"chat_template_kwargs": {"enable_thinking": bool(enabled)}}

    def open(self, path, body=None, method="POST"):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=self.headers(), method=method)
        try:
            return urllib.request.urlopen(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise BackendError(f"{self.label} answered HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, OSError) as e:
            raise BackendError(f"{self.label} is unreachable ({e.__class__.__name__})") from None

    def model_names(self):
        if self.style == "deepseek":
            return list(DEEPSEEK_MODELS)
        try:
            with self.open("/models", method="GET") as r:
                data = json.loads(r.read().decode("utf-8"))
            return [m["id"] for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
        except BackendError:
            return []


class _OpenAIChats:
    def __init__(self, client):
        self.client = client

    def create(self, model=None, history=None, config=None):
        return OpenAIStyleChat(self.client, model, history, config)


class OpenAIStyleChat:
    def __init__(self, client, model, history, config):
        self.client = client
        self.model = model
        self.system, self.temperature, self.thinking = read_config(config)
        self.history = contents_to_messages(history)

    def _body(self, text, stream):
        msgs = ([{"role": "system", "content": self.system}] if self.system else [])
        msgs += self.history + [{"role": "user", "content": text}]
        body = {"model": self.model, "messages": msgs, "stream": stream}
        if self.temperature is not None:
            body["temperature"] = self.temperature
        body.update(self.client.thinking_fields(self.thinking))
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
        if self.client.style == "local" and "<think>" in answer:
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
        use_tags = self.client.style == "local"
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
        self.models = _models_adapter(self)

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
        self.system, self.temperature, self.thinking = read_config(config)
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
        # --setting-sources "" keeps the personal CLAUDE.md and settings out of roleplay replies.
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



# ---------------------------------------------------------------- routing

class MultiClient:
    # Routes by model id prefix. Bare ids go to Gemini, the google-genai client.
    #
    # providers maps 'deepseek' / 'local' / 'claude' to a client, or to None when that backend is off.

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

    def configured(self):
        return {name: (client is not None) for name, client in self.providers.items()}


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

    def generate_content(self, model=None, contents="", **kwargs):
        client, bare = self.multi.route(model)
        if client is self.multi.gemini:
            return client.models.generate_content(model=bare, contents=contents, **kwargs)
        return client.models.generate_content(model=bare, contents=contents)
