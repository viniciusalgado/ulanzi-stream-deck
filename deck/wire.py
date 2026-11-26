"""What crosses between the deck's processes (service <-> agent <-> console helper): values as JSON bytes.

    data = wire.dumps(value)        # bytes
    value = wire.loads(data)        # raises WireError on anything it doesn't know

Values: None, bool, int, float, str, bytes, tuple, list, str-keyed dict, and the state dataclasses listed in
TYPES. Nothing else: the service runs as SYSTEM, so it must never build arbitrary objects from what another
process sent (that is why this isn't pickle). Fields listed in DROP never cross: an Environment's `cli` is a
command line the agent runs, and the service must not even see one it could be tricked into running.
"""
from __future__ import annotations

import base64
import dataclasses
import json

from .sources.headphones import HeadphoneState
from .sources.media import MediaState, Playlist
from .sources.t3 import Environment, Project, T3State, Thread

TYPES = {cls.__name__: cls for cls in (T3State, Project, Thread, Environment, MediaState, Playlist,
                                       HeadphoneState)}
DROP = {"Environment": {"cli"}}


class WireError(ValueError):
    pass


def dumps(value) -> bytes:
    return json.dumps(_enc(value), separators=(",", ":")).encode()


def loads(data: bytes):
    try:
        raw = json.loads(data)
    except (ValueError, UnicodeDecodeError) as e:
        raise WireError(f"not JSON: {e}") from None
    return _dec(raw)


def _enc(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, bytes):
        return {"$b": base64.b64encode(v).decode()}
    if isinstance(v, tuple):
        return {"$t": [_enc(x) for x in v]}
    if isinstance(v, list):
        return [_enc(x) for x in v]
    if isinstance(v, dict):
        if not all(isinstance(k, str) for k in v):
            raise WireError("dict keys must be strings")
        return {"$d": {k: _enc(x) for k, x in v.items()}}
    name = type(v).__name__
    if TYPES.get(name) is type(v):
        drop = DROP.get(name, ())
        return {"$o": name, "f": {f.name: _enc(getattr(v, f.name)) for f in dataclasses.fields(v)
                                  if f.name not in drop}}
    raise WireError(f"can't send a {name}")


def _dec(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, list):
        return [_dec(x) for x in v]
    if not isinstance(v, dict) or len(v) not in (1, 2):
        raise WireError("malformed value")
    if "$b" in v:
        return base64.b64decode(v["$b"])
    if "$t" in v:
        return tuple(_dec(x) for x in _list(v["$t"]))
    if "$d" in v:
        if not isinstance(v["$d"], dict):
            raise WireError("malformed dict")
        return {k: _dec(x) for k, x in v["$d"].items()}
    if "$o" in v:
        cls = TYPES.get(v["$o"])
        if cls is None or not isinstance(v.get("f"), dict):
            raise WireError(f"unknown type {v['$o']!r}")
        allowed = {f.name for f in dataclasses.fields(cls)} - DROP.get(v["$o"], set())
        fields = {k: _dec(x) for k, x in v["f"].items() if k in allowed}
        if v["$o"] == "Environment":
            fields["cli"] = ()
        try:
            return cls(**fields)
        except TypeError as e:
            raise WireError(f"bad {v['$o']}: {e}") from None
    raise WireError("malformed value")


def _list(v):
    if not isinstance(v, list):
        raise WireError("malformed tuple")
    return v
