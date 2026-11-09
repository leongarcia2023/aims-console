#!/usr/bin/env python3
"""build_static.py: a password-gated static copy of the console.

    python static/build_static.py --repo /path/to/AIMS --runs runs_console --out /path/to/site/AIMS \
        [--password ...] [--backend https://aims.example.org --token ...] [--include run ids] [--artifact]

The copy is one HTML document (page, styles, script, the recorded runs and the template inlined)
encrypted with AES-GCM under a key derived from the password (PBKDF2-SHA256, 600,000 iterations),
the same scheme as the DCAF preview on leonmgarcia.com. The output directory gets index.html (the
gate), access.js, manifest.json, app.enc and fonts/.

With --backend (a public-mode console service) and --token (its access token), the page does live
runs there on the visitor's own API key and falls back to replaying the recorded runs when the
service is down. Both values live inside the encrypted body, not in the gate. Without --backend
the page replays and offers a field for a service address.

The password is taken from --password, else from AIMS_STATIC_PASSWORD, else generated and
printed once. Keep it out of the repository.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

CHECK = "AIMS console private copy v1"
WORDS = ("arrow marker stage filter frame scan gate sweep spectra lattice moire twist melt ripple probe "
         "tip lift approach retract centre drift step series packet prior evidence posterior kelvin micron").split()


def passphrase():
    words = list(WORDS)
    picked = [words.pop(secrets.randbelow(len(words))) for _ in range(3)]
    return "-".join(picked) + "-" + str(secrets.randbelow(90) + 10)


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def encrypt(key: bytes, data: bytes):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = secrets.token_bytes(12)
    return iv, AESGCM(key).encrypt(iv, data, None)


def inline_fonts(text: str, fonts_dir: Path) -> str:
    """Replace url(fonts/x.woff2) with a data URI, for hosts that only serve the page's own files inline."""
    def sub(m):
        f = fonts_dir / m.group(1)
        return "url(data:font/woff2;base64," + base64.b64encode(f.read_bytes()).decode() + ")"
    return re.sub(r"url\(fonts/([\w.-]+\.woff2)\)", sub, text)


def run_records(runs_dir: Path):
    """The runs as /api/runs lists them, newest first, plus each run's events."""
    runs, events = [], {}
    for d in sorted(runs_dir.glob("*_*")):
        if not (d / "meta.json").exists():
            continue
        meta = json.loads((d / "meta.json").read_text())
        evs = [json.loads(l) for l in (d / "events.jsonl").read_text().splitlines() if l.strip()]
        res = json.loads((d / "result.json").read_text()) if (d / "result.json").exists() else None
        states = [e for e in evs if e.get("type") == "status"]
        state = states[-1]["state"] if states else "unknown"
        if state in ("running", "connecting", "awaiting_approval"):
            state = "interrupted"
        runs.append({"id": meta["id"], "loop": meta["loop"], "state": state, "model": meta.get("model"),
                     "started": d.stat().st_mtime, "result": res})
        events[meta["id"]] = evs
    runs.sort(key=lambda r: r["started"], reverse=True)
    return runs, events


def tool_list(events):
    """Tool names from the first recorded session that connected to the MCP server."""
    for evs in events.values():
        for e in evs:
            if e.get("type") == "system" and e.get("tools"):
                names = [t.split("mcp__aims__", 1)[1] for t in e["tools"] if t.startswith("mcp__aims__")]
                if names:
                    return [{"name": n, "description": ""} for n in names]
    return []


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", required=True, help="the AIMS repository (for the loop prompts)")
    ap.add_argument("--runs", default=str(ROOT / "runs_console"))
    ap.add_argument("--out", required=True, help="output directory, e.g. the site's AIMS/ folder")
    ap.add_argument("--password", default=os.environ.get("AIMS_STATIC_PASSWORD"))
    ap.add_argument("--mcp-url", default="recorded simulator runs", help="shown in the byline while replaying")
    ap.add_argument("--model", default="as recorded", help="shown in the byline while replaying")
    ap.add_argument("--sim", action="store_true", default=True)
    ap.add_argument("--note", default="This page replays the recorded simulator runs listed under Runs; nothing here reaches an instrument or an agent. With the console service up, the same page runs live.")
    ap.add_argument("--iterations", type=int, default=600_000)
    ap.add_argument("--include", nargs="*", default=None, help="run ids to include (default: every run under --runs)")
    ap.add_argument("--artifact", action="store_true",
                    help="build for a claude.ai artifact: fonts inlined as data URIs, gate without a document skeleton, access.js inline")
    ap.add_argument("--backend", default=None,
                    help="address of a running console service (public mode); the page then does live runs there and keeps the recorded runs as a fallback")
    ap.add_argument("--token", default=os.environ.get("AIMS_ACCESS_TOKEN") or None,
                    help="the service's access token (its --access-token); carried inside the encrypted page, never in the gate")
    ap.add_argument("--runlocal-repo", default=None,
                    help="public repository of the console; the page then shows the run-it-yourself commands (docker and install script)")
    ap.add_argument("--runlocal-image", default=None, help="container image for the docker command, e.g. ghcr.io/owner/aims-console")
    a = ap.parse_args()

    from backend.app import LOOP_FILES, MODELS, SKILLS, load_prompts, template_info, with_skill_steps
    repo = Path(a.repo).resolve()
    runs, events = run_records(Path(a.runs))
    if a.include:
        runs = [r for r in runs if r["id"] in a.include]
        events = {k: v for k, v in events.items() if k in a.include}
    template = template_info(repo)
    if template:        # the navigation template rides inside the encrypted page, re-encoded at full resolution
        import io
        from PIL import Image
        buf = io.BytesIO()
        with Image.open(template["path"]) as im:
            im.convert("RGB").save(buf, "JPEG", quality=80, optimize=True)
        template = {k: v for k, v in template.items() if k != "path"}
        template["url"] = "data:image/jpeg;base64," + b64(buf.getvalue())
    prompts = load_prompts(repo)
    prompts_skills = dict(prompts)
    skills = SKILLS.is_dir()
    if skills:
        prompts_skills["navigation"] = with_skill_steps(prompts["navigation"])
    runlocal = None
    if a.runlocal_repo:
        repo = a.runlocal_repo.rstrip("/")
        raw = repo.replace("github.com", "raw.githubusercontent.com") + "/main/install.sh"
        runlocal = {"repo": repo, "script": f"curl -fsSL {raw} | bash",
                    "docker": f"docker run --rm -p 8080:8080 {a.runlocal_image}" if a.runlocal_image else None}
    config = {"sim": a.sim, "mcp_url": a.mcp_url, "model": a.model, "runs_dir": "runs_console", "runlocal": runlocal,
              "loops": list(LOOP_FILES), "prompt_files": LOOP_FILES, "prompts": prompts_skills, "prompts_plain": prompts,
              "static": True, "template": template, "models": MODELS, "skills": skills,
              "skill_names": sorted(p.name for p in SKILLS.iterdir() if (p / "SKILL.md").exists()) if skills else []}
    data = {"config": config, "tools": tool_list(events), "runs": runs, "events": events, "note": a.note}

    web = ROOT / "web"
    css = (web / "style.css").read_text()
    if a.artifact:
        css = inline_fonts(css, web / "fonts")
    js = (web / "app.js").read_text()
    html = (web / "index.html").read_text()
    html = html.replace('<link rel="stylesheet" href="static/style.css">', "<style>\n" + css + "\n</style>")
    backend_line = f"window.AIMS_BACKEND = {json.dumps(a.backend.rstrip('/'))};" if a.backend else ""
    if a.token:
        backend_line += f"window.AIMS_TOKEN = {json.dumps(a.token)};"
    html = html.replace('<script src="static/app.js"></script>',
                        "<script>" + backend_line + "window.AIMS_STATIC = " + json.dumps(data).replace("</", "<\\/") + ";</script>\n<script>\n" + js + "\n</script>")
    html = html.replace("<title>AIMS console</title>", '<title>AIMS console</title>\n<meta name="robots" content="noindex,nofollow,noarchive">')
    assert 'src="static/' not in html and 'href="static/' not in html, "an unrewritten static/ reference is left in the page"

    password = a.password or passphrase()
    salt = secrets.token_bytes(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, a.iterations, dklen=32)
    iv, payload = encrypt(key, html.encode())
    civ, check = encrypt(key, CHECK.encode())

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"version": 1, "kdf": "PBKDF2-SHA256", "iterations": a.iterations, "salt": b64(salt), "iv": b64(iv),
                "payload": "app.enc", "checkIv": b64(civ), "check": b64(check)}
    if a.artifact:      # the artifact host serves text and known media types only: ship the ciphertext as base64 text
        (out / "app.txt").write_text(b64(payload))
        manifest.update(payload="app.txt", encoding="base64")
    else:
        (out / "app.enc").write_bytes(payload)
    (out / "manifest.json").write_text(json.dumps(manifest))
    gate = (HERE / "gate.html").read_text()
    access = (HERE / "access.js").read_text()
    if a.artifact:
        # the artifact host wraps the file in its own document skeleton and only allows the page's own
        # files over relative fetches, so: no doctype/html/head/body, script inline, fonts inline
        gate = inline_fonts(gate, web / "fonts")
        gate = re.sub(r"^.*?<title>", "<title>", gate, count=1, flags=re.S)
        gate = gate.replace("</head>\n<body>", "").replace("</body>\n</html>", "").replace("</head>", "").replace("<body>", "")
        gate = gate.replace('<script src="access.js"></script>', "<script>\n" + access + "\n</script>")
        (out / "index.html").write_text(gate.strip() + "\n")
    else:
        (out / "index.html").write_text(gate)
        (out / "access.js").write_text(access)
        fonts = out / "fonts"
        fonts.mkdir(exist_ok=True)
        for f in (web / "fonts").iterdir():
            shutil.copy(f, fonts / f.name)
    print(f"wrote {out}: page {len(html) // 1024} KB, {len(runs)} runs, {sum(len(v) for v in events.values())} events")
    if not a.password:
        print("password (generated, not stored):", password)


if __name__ == "__main__":
    main()
