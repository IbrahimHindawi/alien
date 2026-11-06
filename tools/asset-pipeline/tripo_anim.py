"""Tripo rigging and animation for the asset pipeline (system Python).

    python tools/asset-pipeline/tripo_anim.py check <name>
    python tools/asset-pipeline/tripo_anim.py rig <name> <rig_type> <rig_model>
    python tools/asset-pipeline/tripo_anim.py retarget <name> <rig_task_id> <preset> [<preset> ...]

API (v3, https://openapi.tripo3d.ai/v3, Bearer key from TRIPO_API_KEY):
POST /animations/rig-check {input}            -> riggable, rig_type (free)
POST /animations/rig {input, model, rig_type, out_format}
POST /animations/retarget {input: rig task, animations: [...<=5], animate_in_place, out_format}
Every task is logged in anim_attempts.json; GLBs go to cache/<task_id>/model.glb.
The rig input is the accepted text-to-model task id from selection.json.
"""
import datetime
import json
import os
import sys

import requests

import tripo

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "anim_attempts.json")


def _post(path, body):
    return tripo._check(requests.post(tripo.BASE + path, headers=tripo._headers(), json=body, timeout=60))["task_id"]


def _log(name, rec):
    log = tripo._load(LOG)
    lst = log.setdefault(name, [])
    for i, x in enumerate(lst):
        if x.get("task_id") == rec.get("task_id"):
            lst[i] = dict(x, **rec)
            break
    else:
        lst.append(rec)
    tripo._save(LOG, log)


def run(name, kind, path, body):
    rec = {"kind": kind, "request": body,
           "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    try:
        rec["task_id"] = _post(path, body)
    except (tripo.TripoError, requests.RequestException) as e:
        rec["status"], rec["error"] = "create failed", str(e)
        _log(name, rec)
        print("%s %s: %s" % (name, kind, e), flush=True)
        return rec
    rec["status"] = "submitted"
    _log(name, rec)
    print("%s %s: task %s submitted" % (name, kind, rec["task_id"]), flush=True)
    d = tripo.wait_task(rec["task_id"], timeout=1800)
    rec["status"] = d.get("status")
    rec["credits"] = d.get("credits_consumed")
    rec["output"] = {k: v for k, v in (d.get("output") or {}).items() if not k.endswith("_url")}
    if d.get("status") == "success" and (d.get("output") or {}).get("model_url"):
        rec["cache"] = os.path.relpath(tripo.fetch(rec["task_id"], d), HERE).replace("\\", "/")
    elif d.get("status") != "success":
        rec["error"] = "%s %s" % (d.get("error_code"), d.get("error_message"))
    _log(name, rec)
    print("%s %s: task %s %s, credits %s %s" % (name, kind, rec["task_id"], rec["status"], rec.get("credits"),
                                                rec.get("error", "")), flush=True)
    return rec


def main(argv):
    cmd, name = argv[0], argv[1]
    sel = json.load(open(os.path.join(HERE, "selection.json"), encoding="utf-8"))
    src = sel[name]["task_id"]
    if cmd == "check":
        r = run(name, "rig-check", "/animations/rig-check", {"input": src})
    elif cmd == "rig":
        r = run(name, "rig", "/animations/rig", {"input": src, "rig_type": argv[2], "model": argv[3], "out_format": "glb"})
    elif cmd == "retarget":
        r = run(name, "retarget", "/animations/retarget",
                {"input": argv[2], "animations": argv[3:], "animate_in_place": True, "out_format": "glb",
                 "export_with_geometry": True})
    else:
        raise SystemExit("unknown command")
    return 0 if r.get("status") == "success" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
