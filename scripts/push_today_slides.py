#!/usr/bin/env python3
"""
push_today_slides.py
====================

Pushne dnesni instagram slidy (slide_01..08.png) a caption.txt do GitHubu
repozitare kevinozer/ai-news pres GitHub Git Data API.

Vyhody oproti puvodnimu push_today_slides.ps1:
  * Vubec se nedotyka lokalniho .git/  -> zaseknuty rebase, lock files,
    OneDrive sync, untracked kolize, nic z toho nemuze tohle rozbit.
  * Atomicky commit (1 commit pro vsech 9 souboru) primo do origin/main.
  * Bezi nativne v Linux sandboxu Claude scheduled tasku.

Vyzaduje:
  * AI_NEWS_PUSH_TOKEN v .env (v korenove slozce repa), fine-grained PAT
    s Contents: Read and write pro kevinozer/ai-news.
  * Python 3 + 'requests' (pip install requests --break-system-packages).

Exit kody (kompatibilni s puvodnim push_today_slides.ps1):
   0 = SUCCESS (commit prosel) NEBO SKIP (nic noveho, vse uz pushnuto)
   2 = dnesni slozka instagram/YYYY-MM-DD/ neexistuje nebo neni komplet
   3 = chyba pri cteni .env / chybi token
   4 = chyba pri cteni lokalnich souboru
   5 = GitHub API chyba pri GET (ref / commit / tree / blob)
   6 = GitHub API chyba pri PUT/POST (vytvoreni blob / tree / commit / refs)

Pouziti:
   python3 scripts/push_today_slides.py                # dnesni datum
   python3 scripts/push_today_slides.py 2026-05-24     # konkretni datum
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("[push] CHYBA: chybi 'requests'. Nainstaluj: pip install requests --break-system-packages")
    sys.exit(3)


REPO_OWNER = "kevinozer"
REPO_NAME = "ai-news"
BRANCH = "main"
API_BASE = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / ".env"


def log(msg: str) -> None:
    print(f"[push] {msg}", flush=True)


def load_env_var(name: str) -> str | None:
    """Minimalisticky .env parser - nepotrebujeme python-dotenv."""
    if not ENV_FILE.exists():
        return os.environ.get(name)
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == name:
            return v.strip().strip('"').strip("'")
    return os.environ.get(name)


def gh_request(
    method: str,
    path: str,
    token: str,
    json_body: dict | None = None,
    expect: tuple[int, ...] = (200,),
    retries: int = 3,
) -> dict:
    """GitHub API request s jednoduchym retry (rate limit, transient 5xx)."""
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "ai-news-push-slides/1.0",
    }
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.request(method, url, headers=headers, json=json_body, timeout=30)
        except requests.RequestException as exc:
            last_err = f"network error ({exc})"
            if attempt < retries:
                time.sleep(2 * attempt)
                continue
            raise SystemExit(f"[push] {method} {url} -> {last_err}")
        if r.status_code in expect:
            return r.json() if r.text else {}
        # Retry na 5xx / 429 / 403 rate limit
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
            time.sleep(2 * attempt)
            continue
        if r.status_code == 403 and "rate limit" in r.text.lower() and attempt < retries:
            time.sleep(10)
            continue
        body = r.text[:300]
        raise SystemExit(
            f"[push] {method} {url} -> HTTP {r.status_code} (expected {expect})\n         body: {body}"
        )
    raise SystemExit(f"[push] {method} {url} -> selhalo po {retries} pokusech ({last_err})")


def collect_today_files(date_str: str) -> list[tuple[str, bytes, str]]:
    """Vrati list (repo_path, content_bytes, sha1_of_blob) pro vsech 9 souboru."""
    day_dir = REPO_ROOT / "instagram" / date_str
    if not day_dir.exists():
        log(f"CHYBA: dnesni slozka neexistuje: {day_dir}")
        sys.exit(2)

    expected = [f"slide_{i:02d}.png" for i in range(1, 9)] + ["caption.txt"]
    files: list[tuple[str, bytes, str]] = []
    missing: list[str] = []
    for name in expected:
        f = day_dir / name
        if not f.exists():
            missing.append(name)
            continue
        try:
            data = f.read_bytes()
        except OSError as exc:
            log(f"CHYBA: nelze cist {f}: {exc}")
            sys.exit(4)
        # Git blob SHA = sha1("blob <len>\0<content>")
        header = f"blob {len(data)}\0".encode("utf-8")
        sha = hashlib.sha1(header + data).hexdigest()
        repo_path = f"instagram/{date_str}/{name}"
        files.append((repo_path, data, sha))

    if missing:
        log(f"CHYBA: chybi soubory v {day_dir.name}: {', '.join(missing)}")
        sys.exit(2)
    return files


def needs_update(files: list[tuple[str, bytes, str]], token: str) -> list[tuple[str, bytes, str]]:
    """Vrati podmnozinu souboru, ktere se v repu lisi (nebo neexistuji)."""
    to_upload: list[tuple[str, bytes, str]] = []
    for repo_path, data, local_sha in files:
        url = f"{API_BASE}/contents/{repo_path}?ref={BRANCH}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-news-push-slides/1.0",
        }
        try:
            r = requests.get(url, headers=headers, timeout=30)
        except requests.RequestException as exc:
            log(f"WARN: nelze overit {repo_path}, predpokladam upload: {exc}")
            to_upload.append((repo_path, data, local_sha))
            continue
        if r.status_code == 404:
            to_upload.append((repo_path, data, local_sha))
            continue
        if r.status_code != 200:
            log(f"WARN: GET contents/{repo_path} -> {r.status_code}, predpokladam upload")
            to_upload.append((repo_path, data, local_sha))
            continue
        remote_sha = r.json().get("sha")
        if remote_sha != local_sha:
            to_upload.append((repo_path, data, local_sha))
    return to_upload


def main() -> int:
    if len(sys.argv) > 1:
        date_str = sys.argv[1]
    else:
        # UTC datum -- shoda s puvodnim PowerShell skriptem ktery pouziva tvuj
        # lokalni datum, ale pro Linux sandbox je UTC bezpecnejsi (sandbox nemusi
        # byt v CEST). Pokud by tohle delalo problem na hranici dne, prepni na:
        #   datetime.now().strftime("%Y-%m-%d")
        date_str = datetime.now().strftime("%Y-%m-%d")

    log(f"Today: {date_str}")
    log(f"Repo:  {REPO_OWNER}/{REPO_NAME}@{BRANCH}")

    token = load_env_var("AI_NEWS_PUSH_TOKEN")
    if not token:
        log("CHYBA: AI_NEWS_PUSH_TOKEN neni v .env ani v env. Vygeneruj fine-grained PAT.")
        return 3

    files = collect_today_files(date_str)
    log(f"OK: {len(files)} souboru pripraveno k uploadu")

    # 1) Optimalizace: pokud uz jsou vsechny soubory v repu se shodnym SHA, skip
    to_upload = needs_update(files, token)
    if not to_upload:
        log("Nic noveho ke commitu (vse uz je v repu se stejnym obsahem).")
        log("Hotovo (SKIP).")
        return 0
    log(f"Z toho {len(to_upload)} souboru potrebuje upload.")

    # 2) Ziskej aktualni ref a tree
    try:
        ref = gh_request("GET", f"/git/ref/heads/{BRANCH}", token)
        parent_sha = ref["object"]["sha"]
        commit = gh_request("GET", f"/git/commits/{parent_sha}", token)
        base_tree_sha = commit["tree"]["sha"]
        log(f"Parent commit: {parent_sha[:8]}, base tree: {base_tree_sha[:8]}")
    except SystemExit:
        return 5

    # 3) Upload blobu (vrati blob SHA, pak je odkazujeme v tree)
    tree_entries = []
    for repo_path, data, _local_sha in to_upload:
        b64 = base64.b64encode(data).decode("ascii")
        try:
            blob = gh_request(
                "POST",
                "/git/blobs",
                token,
                json_body={"content": b64, "encoding": "base64"},
                expect=(201,),
            )
        except SystemExit:
            return 6
        tree_entries.append({
            "path": repo_path,
            "mode": "100644",
            "type": "blob",
            "sha": blob["sha"],
        })
        log(f"  + {repo_path} ({len(data)} B, blob {blob['sha'][:8]})")

    # 4) Vytvor novy tree s base_tree (zachova vse ostatni)
    try:
        new_tree = gh_request(
            "POST",
            "/git/trees",
            token,
            json_body={"base_tree": base_tree_sha, "tree": tree_entries},
            expect=(201,),
        )
    except SystemExit:
        return 6
    log(f"Novy tree: {new_tree['sha'][:8]}")

    # 5) Vytvor commit
    msg = f"chore(ig): denni slidy {date_str}"
    try:
        new_commit = gh_request(
            "POST",
            "/git/commits",
            token,
            json_body={
                "message": msg,
                "tree": new_tree["sha"],
                "parents": [parent_sha],
            },
            expect=(201,),
        )
    except SystemExit:
        return 6
    new_sha = new_commit["sha"]
    log(f"Novy commit: {new_sha[:8]}  '{msg}'")

    # 6) Update ref (fast-forward)
    try:
        gh_request(
            "PATCH",
            f"/git/refs/heads/{BRANCH}",
            token,
            json_body={"sha": new_sha, "force": False},
            expect=(200,),
        )
    except SystemExit:
        return 6

    commit_url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/commit/{new_sha}"
    log(f"Hotovo. {date_str} je v GitHubu: {commit_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
