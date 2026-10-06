"""Voice backends.

The interface is deliberately small - three things a backend must do:

    health()                         -> (ready: bool, detail: str)
    transcribe(path)                 -> str
    ensure_profile(name, samples)    -> profile_id
    generate(profile_id, text, dest, **opts)

Only Voicebox ships today, because it's the one that's actually been run
end to end. Register another with @backend("name").
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Callable

_REGISTRY: dict[str, type] = {}


def backend(name: str) -> Callable[[type], type]:
    def deco(cls: type) -> type:
        _REGISTRY[name] = cls
        cls.backend_name = name
        return cls
    return deco


def get(name: str, cfg):
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError("Unknown voice backend %r. Available: %s"
                         % (name, ", ".join(sorted(_REGISTRY)) or "none"))
    return cls(cfg)


class BackendError(RuntimeError):
    pass


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    out: list[bytes] = []
    for k, v in fields.items():
        if v is None:
            continue
        out += [b"--" + b.encode(),
                ('Content-Disposition: form-data; name="%s"' % k).encode(), b"",
                str(v).encode()]
    for k, (fname, content, ctype) in files.items():
        out += [b"--" + b.encode(),
                ('Content-Disposition: form-data; name="%s"; filename="%s"' % (k, fname)).encode(),
                ("Content-Type: " + ctype).encode(), b"", content]
    out += [("--" + b + "--").encode(), b""]
    return b"\r\n".join(out), "multipart/form-data; boundary=" + b


def _detail(raw: bytes) -> str:
    try:
        js = json.loads(raw.decode())
        d = js.get("detail", js)
        return d if isinstance(d, str) else json.dumps(d)[:300]
    except Exception:
        return raw[:300].decode(errors="ignore")


@backend("voicebox")
class Voicebox:
    """Local Voicebox (https://localhost:17493). Nothing leaves the machine."""

    DONE = ("", "complete", "completed", "done", "success", "succeeded")

    def __init__(self, cfg):
        self.cfg = cfg
        self.base = cfg.url.rstrip("/")

    # ---- http
    def _call(self, method, path, body=None, headers=None, timeout=900):
        data = None
        headers = dict(headers or {})
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        elif isinstance(body, bytes):
            data = body
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type", ""), e.read()
        except urllib.error.URLError as e:
            raise BackendError(
                "Can't reach Voicebox at %s (%s).\n"
                "Open the Voicebox app and wait for it to finish loading."
                % (self.base, e.reason)) from None

    def _json(self, method, path, body=None, timeout=900):
        st, ct, raw = self._call(method, path, body, timeout=timeout)
        try:
            js = json.loads(raw.decode() or "null")
        except Exception:
            js = None
        return st, js, raw

    # ---- interface
    def health(self) -> tuple[bool, str]:
        try:
            st, js, _ = self._json("GET", "/health", timeout=10)
        except BackendError as e:
            return False, str(e).splitlines()[0]
        if st != 200 or not isinstance(js, dict):
            return False, "Voicebox answered %s" % st
        if js.get("status") != "healthy":
            return False, "status=%s" % js.get("status")
        # "running" is not "ready" - a loaded model is what actually matters.
        if not js.get("model_loaded"):
            return False, ("no voice model loaded - open Voicebox, install a model "
                           "(Qwen3-TTS recommended) and let it finish loading")
        return True, "%s on %s" % (js.get("model_size") or "model",
                                   js.get("gpu_type") or "cpu")

    def transcribe(self, path: str) -> str:
        body, ct = _multipart(
            {"language": self.cfg.language},
            {"file": (os.path.basename(path), open(path, "rb").read(), "audio/wav")})
        st, _, raw = self._call("POST", "/transcribe", body, {"Content-Type": ct})
        if st >= 300:
            raise BackendError("Transcribing %s failed (%s): %s"
                               % (os.path.basename(path), st, _detail(raw)))
        try:
            return (json.loads(raw.decode()).get("text") or "").strip()
        except Exception:
            return ""

    def ensure_profile(self, name: str, samples: list[dict], profile_id: str | None = None) -> str:
        if profile_id:
            st, _, _ = self._json("GET", "/profiles/" + profile_id, timeout=30)
            if st != 200:
                raise BackendError("Voice profile %s not found (status %s)." % (profile_id, st))
            return profile_id

        st, js, _ = self._json("GET", "/profiles", timeout=30)
        items = js if isinstance(js, list) else (js or {}).get("profiles", [])
        for p in items or []:
            if isinstance(p, dict) and p.get("name") == name:
                return str(p.get("id"))

        body = {"name": name, "language": self.cfg.language,
                "description": "Created by bowerbird"}
        if self.cfg.engine:
            body["default_engine"] = self.cfg.engine
        st, js, raw = self._json("POST", "/profiles", body)
        if st >= 300 or not isinstance(js, dict) or "id" not in js:
            raise BackendError("Creating the voice profile failed (%s): %s" % (st, _detail(raw)))
        pid = str(js["id"])

        for s in samples:
            fname = os.path.basename(s["file"])
            body, ct = _multipart(
                {"reference_text": s["text"]},
                {"file": (fname, open(s["file"], "rb").read(), "audio/wav")})
            st, _, raw = self._call("POST", "/profiles/%s/samples" % pid, body,
                                    {"Content-Type": ct})
            if st >= 300:
                raise BackendError("Uploading %s failed (%s): %s" % (fname, st, _detail(raw)))
            print("  added sample %s" % fname, flush=True)
        return pid

    def generate(self, profile_id: str, text: str, dest_wav: str,
                 engine=None, seed=None, instruct=None, to_wav=None) -> None:
        body = {"profile_id": profile_id, "text": text, "language": self.cfg.language}
        if seed is not None:
            body["seed"] = seed
        if instruct:
            body["instruct"] = instruct
        if engine:
            body["engine"] = engine
        st, js, raw = self._json("POST", "/generate", body)
        if st >= 300 or not isinstance(js, dict):
            raise BackendError("Generation failed (%s): %s" % (st, _detail(raw)))
        if str(js.get("status", "")).lower() in ("failed", "error"):
            raise BackendError("Generation failed: %s" % (js.get("error") or "no reason given"))

        # Same machine: Voicebox usually has already written the file.
        ap = js.get("audio_path")
        if ap and os.path.exists(ap) and str(js.get("status", "")).lower() in self.DONE:
            to_wav(ap, dest_wav)
            return

        gid = str(js.get("id") or js.get("generation_id") or "")
        if not gid:
            raise BackendError("No generation id and no audio_path: %s" % _detail(raw))

        deadline = time.time() + 900
        while time.time() < deadline:
            st, sj, _ = self._json("GET", "/history/" + gid, timeout=60)
            if st == 200 and isinstance(sj, dict):
                if str(sj.get("status", "")).lower() in ("failed", "error"):
                    raise BackendError("Generation failed: %s" % (sj.get("error") or "?"))
                ap = sj.get("audio_path")
                if ap and os.path.exists(ap) and str(sj.get("status", "")).lower() in self.DONE:
                    to_wav(ap, dest_wav)
                    return
            st, ct, raw = self._call("GET", "/audio/" + gid, timeout=120)
            if st == 200 and raw and "json" not in ct and "html" not in ct:
                tmp = dest_wav + ".part"
                open(tmp, "wb").write(raw)
                to_wav(tmp, dest_wav)
                os.remove(tmp)
                return
            time.sleep(1.5)
        raise BackendError("Timed out after 15 minutes waiting for generation %s" % gid)
