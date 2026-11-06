"""Tripo3D client for the asset pipeline (system Python, requests).

    python tools/asset-pipeline/tripo.py balance
    python tools/asset-pipeline/tripo.py generate scuttler [--model P1-20260311] [--face-limit N] [--prompt-extra "..."]
    python tools/asset-pipeline/tripo.py fetch <task_id>      # re-download a finished task into the cache

API: https://openapi.tripo3d.ai/v3 (Bearer key). POST /generation/text-to-model
creates a task, GET /tasks/{id} polls it, output.model_url is a GLB. The key
is read from TRIPO_API_KEY at call time and never printed or stored. Every
attempt is appended to attempts.json (task id, model, prompt, request
without the key, status, credits) before and after polling; downloads go to
cache/<task_id>/ (git-ignored), and a cached task is never fetched twice.
"""
import argparse
import datetime
import json
import os
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
ATTEMPTS = os.path.join(HERE, "attempts.json")
BASE = "https://openapi.tripo3d.ai/v3"
DEFAULT_MODEL = "P1-20260311"


class TripoError(RuntimeError):
    pass


def _headers():
    key = os.environ.get("TRIPO_API_KEY")
    if not key:
        raise TripoError("TRIPO_API_KEY is not set")
    return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}


def _check(r):
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text[:500]}
    if r.status_code != 200 or body.get("code") not in (0, None):
        raise TripoError("HTTP %d from %s: %s" % (r.status_code, r.url.split("?")[0], json.dumps(body)[:800]))
    return body.get("data", body)


def balance():
    return _check(requests.get(BASE + "/account/balance", headers=_headers(), timeout=30))


def create_task(req):
    return _check(requests.post(BASE + "/generation/text-to-model", headers=_headers(), json=req, timeout=60))["task_id"]


def get_task(task_id):
    return _check(requests.get(BASE + "/tasks/" + task_id, headers=_headers(), timeout=30))


def wait_task(task_id, timeout=1200, poll=6):
    t0 = time.time()
    while True:
        d = get_task(task_id)
        st = d.get("status")
        if st in ("success", "failed", "cancelled", "banned", "expired"):
            return d
        if time.time() - t0 > timeout:
            raise TripoError("task %s still %s after %ds" % (task_id, st, timeout))
        time.sleep(poll)


def _download(url, path):
    r = requests.get(url, timeout=300)
    if r.status_code != 200:
        raise TripoError("download HTTP %d" % r.status_code)
    with open(path, "wb") as f:
        f.write(r.content)


def cached_model(task_id):
    p = os.path.join(CACHE, task_id, "model.glb")
    return p if os.path.exists(p) else None


def fetch(task_id, data=None):
    """Download a finished task's model and preview into cache/<task_id>/."""
    d = data or get_task(task_id)
    if d.get("status") != "success":
        raise TripoError("task %s is %s" % (task_id, d.get("status")))
    out = os.path.join(CACHE, task_id)
    os.makedirs(out, exist_ok=True)
    o = d.get("output", {})
    if not cached_model(task_id):
        url = o.get("model_url") or o.get("pbr_model_url") or o.get("model")
        if not url:
            raise TripoError("task %s has no model url in output keys %s" % (task_id, sorted(o)))
        _download(url, os.path.join(out, "model.glb"))
    img = o.get("rendered_image_url")
    if img and not os.path.exists(os.path.join(out, "rendered.png")):
        try:
            _download(img, os.path.join(out, "rendered.png"))
        except TripoError:
            pass
    meta = {k: v for k, v in d.items() if k != "output"}
    meta["output_keys"] = sorted(o)
    with open(os.path.join(out, "task.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return os.path.join(out, "model.glb")


def _load(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def generate_attempt(name, spec, attempts_path=ATTEMPTS, model=DEFAULT_MODEL, face_limit=None,
                     prompt_extra="", texture_quality="standard"):
    """One text-to-model generation for an asset; returns the attempt record."""
    prompt = spec["prompt"] + ((" " + prompt_extra) if prompt_extra else "")
    req = {"model": model, "prompt": prompt[:1024], "negative_prompt": spec.get("negative_prompt", "")[:255],
           "texture": True, "pbr": False, "texture_quality": texture_quality,
           "face_limit": int(face_limit or spec["budgets"][0])}
    attempts = _load(attempts_path)
    rec = {"task_id": None, "model_version": model, "prompt": req["prompt"], "negative_prompt": req["negative_prompt"],
           "request": req, "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "status": "creating"}
    try:
        rec["task_id"] = create_task(req)
    except (TripoError, requests.RequestException) as e:
        rec["status"] = "create failed"
        rec["error"] = str(e)
        attempts.setdefault(name, []).append(rec)
        _save(attempts_path, attempts)
        raise
    rec["status"] = "submitted"
    attempts.setdefault(name, []).append(rec)
    _save(attempts_path, attempts)
    print("%s: task %s submitted (%s, face_limit %d)" % (name, rec["task_id"], model, req["face_limit"]), flush=True)
    d = wait_task(rec["task_id"])
    rec["status"] = d.get("status")
    rec["credits"] = d.get("credits_consumed")
    rec["completed"] = d.get("completed_at")
    if d.get("status") == "success":
        rec["cache"] = os.path.relpath(fetch(rec["task_id"], d), HERE).replace("\\", "/")
    else:
        rec["error"] = "%s %s" % (d.get("error_code"), d.get("error_message"))
    attempts = _load(attempts_path)
    lst = attempts.setdefault(name, [])
    if any(x.get("task_id") == rec["task_id"] for x in lst):
        attempts[name] = [dict(x, **rec) if x.get("task_id") == rec["task_id"] else x for x in lst]
    else:  # a concurrent writer dropped the submitted record: put it back
        lst.append(rec)
    _save(attempts_path, attempts)
    print("%s: task %s %s, credits %s" % (name, rec["task_id"], rec["status"], rec.get("credits")), flush=True)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["balance", "generate", "fetch"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--face-limit", type=int)
    ap.add_argument("--prompt-extra", default="")
    a = ap.parse_args()
    if a.cmd == "balance":
        print(json.dumps(balance()))
    elif a.cmd == "fetch":
        print(fetch(a.arg))
    else:
        sys.path.insert(0, HERE)
        import mdmlib
        spec = mdmlib.load_specs()["assets"][a.arg]
        generate_attempt(a.arg, spec, model=a.model, face_limit=a.face_limit, prompt_extra=a.prompt_extra)
    return 0


if __name__ == "__main__":
    sys.exit(main())
