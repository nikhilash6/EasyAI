"""Check every URL in the catalogue actually points at the right file.

    python tools/verify_urls.py            check the original sources
    python tools/verify_urls.py --mirror   check our own copies instead

Sends a HEAD to each model URL and compares the size the server reports with
the size of that file on this machine. A link that resolves but returns a
different size is pointing at a different build - a different quantisation, a
different checkpoint - and that is the worst failure this installer can have,
because it downloads successfully and then produces wrong output.

Also reports which files still have no URL at all.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "setup" / "catalog.json"

#: HuggingFace answers a HEAD on /resolve/ with a redirect to a CDN; the size
#: comes back on the final response, so redirects must be followed.
TIMEOUT = 30


def human(n: int | None) -> str:
    if not n:
        return "?"
    return f"{n / 1024 ** 3:.2f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


#: Addresses that work on the machine that uploaded the files and nowhere else.
#: A mirror link pointing at one of these is silently broken for every viewer.
PRIVATE = ("localhost", "127.", "0.0.0.0", "10.", "192.168.", "169.254.",
           *(f"172.{n}." for n in range(16, 32)))


def is_private(url: str) -> bool:
    host = url.split("/")[2].split(":")[0].lower() if "//" in url else ""
    return any(host == p.rstrip(".") or host.startswith(p) for p in PRIVATE)


def check_mirrors(session, models: dict) -> int:
    """Verify our own copies: size, resume support, and reachability."""
    entries = [(n, e) for n, e in models.items() if e.get("mirror")]
    if not entries:
        print("No mirrored models in the catalogue.")
        return 0

    print(f"Checking {len(entries)} mirrored models\n")
    good, bad = 0, []
    for name, entry in entries:
        short = Path(name).name
        url = entry["mirror"]
        expected = entry.get("bytes", 0)
        problems = []

        if is_private(url):
            problems.append("points at a private address - no viewer can reach it")

        try:
            r = session.head(url, allow_redirects=True, timeout=TIMEOUT)
            size = int(r.headers.get("Content-Length") or 0)
            if r.status_code >= 400:
                problems.append(f"HTTP {r.status_code}")
            elif expected and size != expected:
                problems.append(f"size {size:,}, expected {expected:,}")
        except requests.RequestException as e:
            size = 0
            problems.append(f"unreachable ({type(e).__name__})")

        # Ask for a real range rather than trusting Accept-Ranges: ownCloud
        # honours Range without ever advertising it, and a header-only check
        # would wrongly condemn a perfectly resumable link.
        if not problems:
            try:
                r = session.get(url, headers={"Range": "bytes=100-199"},
                                stream=True, timeout=TIMEOUT)
                body = r.raw.read(300, decode_content=True)
                r.close()
                if r.status_code != 206 or len(body) != 100:
                    problems.append(
                        f"no resume support (asked for 100 bytes, got HTTP "
                        f"{r.status_code} and {len(body)} bytes) - an "
                        f"interrupted download would restart from zero")
            except requests.RequestException as e:
                problems.append(f"range request failed ({type(e).__name__})")

        if problems:
            bad.append((short, problems))
            print(f"  FAIL {short[:56]:<56} {'; '.join(problems)}")
        else:
            good += 1
            print(f"  ok   {short[:56]:<56} {human(size)}  resumable")

    unmirrored = [n for n, e in models.items() if e.get("gated") and not e.get("mirror")]
    print(f"\n{good} mirrored models verified, {len(bad)} with problems")
    if unmirrored:
        print(f"\nSTILL NEEDS AN ACCOUNT KEY - not on the mirror yet:")
        for n in unmirrored:
            print(f"  {n}  ({models[n].get('bytes', 0):,} bytes)")
    return 1 if bad else 0


def main() -> int:
    if not CATALOG.is_file():
        print("No catalogue - run tools/make_catalog.py first.")
        return 1
    if "--mirror" in sys.argv:
        catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        session = requests.Session()
        session.headers["User-Agent"] = "EasyAISetup/1.0"
        return check_mirrors(session, catalog["models"])
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    models: dict = catalog["models"]

    session = requests.Session()
    session.headers["User-Agent"] = "EasyAISetup/1.0"

    ok, wrong, failed, no_url = [], [], [], []
    for name, entry in models.items():
        url = entry.get("url")
        expected = entry.get("bytes")
        if not url:
            no_url.append(name)
            continue
        try:
            r = session.head(url, allow_redirects=True, timeout=TIMEOUT)
            if r.status_code >= 400:
                failed.append((name, f"HTTP {r.status_code}"))
                continue
            size = int(r.headers.get("Content-Length") or 0)
        except requests.RequestException as e:
            failed.append((name, type(e).__name__))
            continue

        if not expected or not size:
            ok.append((name, size))
        elif size == expected:
            ok.append((name, size))
        else:
            wrong.append((name, expected, size))
        print(f"  {'ok  ' if (name, size) in ok else 'DIFF'} {Path(name).name[:56]:<56}"
              f" {human(size)}")

    print(f"\n{len(ok)} verified, {len(wrong)} size mismatch, "
          f"{len(failed)} unreachable, {len(no_url)} still need a URL")

    if wrong:
        print("\nSIZE MISMATCH - these point at a different build:")
        for name, exp, got in wrong:
            print(f"  {name}\n     here {human(exp)}   server {human(got)}")
    if failed:
        print("\nUNREACHABLE:")
        for name, why in failed:
            print(f"  {name}  ({why})")
    if no_url:
        print("\nNO URL YET - add to setup/url_overrides.json:")
        for name in no_url:
            print(f"  {name}")

    return 1 if (wrong or failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
