"""Tiny OpenAI-compatible chat client (stdlib only, no pip installs).

Builder model  : Meta Model API  (MODEL_API_KEY,  default https://api.meta.ai/v1, muse-spark-1.3)
Reflector model: optional second provider (e.g. Subquadratic) via REFLECT_* env vars;
                 falls back to the builder model if not set.
"""
import json
import os
import re
import time
import urllib.request
import urllib.error


class LLMError(Exception):
    pass


class Client:
    def __init__(self, base_url, api_key, model, label, reasoning_effort=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.label = label
        self.reasoning_effort = reasoning_effort

    def chat(self, system, user, max_tokens=8000, retries=2):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens,
        }
        # muse-spark-1.3 is a reasoning model: at default effort it can spend the whole
        # token budget thinking and return empty content. "low" returns code in ~25 s.
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        data = json.dumps(body).encode()
        last = None
        for i in range(retries + 1):
            req = urllib.request.Request(
                self.base_url + "/chat/completions", data=data, method="POST",
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    out = json.loads(r.read().decode())
                msg = out["choices"][0]["message"]
                content = msg.get("content") or ""
                if isinstance(content, list):  # some APIs return content parts
                    content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                if not content.strip():
                    last = f"empty response (finish_reason={out['choices'][0].get('finish_reason')})"
                    continue
                return content
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}: {e.read().decode(errors='replace')[:500]}"
                if e.code in (400, 401, 403, 404):
                    break
            except Exception as e:  # network / timeout / parse
                last = repr(e)
            time.sleep(1.5 * (i + 1))
        raise LLMError(f"{self.label} call failed: {last}")


def builder_client():
    key = os.environ.get("MODEL_API_KEY")
    if not key:
        return None
    return Client(os.environ.get("MODEL_BASE_URL", "https://api.meta.ai/v1"), key,
                  os.environ.get("MODEL_NAME", "muse-spark-1.3"), "Meta Muse",
                  os.environ.get("MODEL_REASONING_EFFORT", "low") or None)


def reflector_client():
    key = os.environ.get("REFLECT_API_KEY")
    if key:
        return Client(os.environ.get("REFLECT_BASE_URL", "https://api.subq.ai/v1"), key,
                      os.environ.get("REFLECT_MODEL", "subq-preview"), "Subquadratic")
    return builder_client()


def extract_code(text):
    m = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    if m:
        return max(m, key=len).strip()
    return text.strip()


def extract_json(text):
    m = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    cands = m + [text]
    for c in cands:
        s, e = c.find("{"), c.rfind("}")
        if s != -1 and e > s:
            try:
                return json.loads(c[s:e + 1])
            except Exception:
                continue
    raise LLMError("model did not return valid JSON")
