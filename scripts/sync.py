#!/usr/bin/env python3
"""Copy the games letsplay2048.lol has finished into v1/, and refresh the README's results.

Run daily by .github/workflows/sync.yml. Prints a commit message, or nothing when
there was no new game.
"""

import json
import pathlib
import statistics
import time
import urllib.error
import urllib.request

SITE = "https://letsplay2048.lol/data"
USER_AGENT = "letsplay2048-sync (+https://github.com/mprpic/letsplay2048)"
ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN = ROOT / "v1"
GAMES = RUN / "games"
README = ROOT / "README.md"
START, END = "<!-- results:start -->", "<!-- results:end -->"


def fetch(path) -> bytes:
    request = urllib.request.Request(f"{SITE}/{path}", headers={"User-Agent": USER_AGENT})
    for _ in range(2):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError):
            time.sleep(10)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def results(games, baseline):
    """The README's table, a row per model in the order they played, then random play."""
    by_model = {}
    for game in games:
        by_model.setdefault(game["model"], []).append(game)
    rows = [
        "| Model | Games | Average | Median | Best | Highest tile | Reached 512 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for model, played in by_model.items():
        scores = [game["score"] for game in played]
        reached = sum(game["tile"] >= 512 for game in played) / len(played)
        rows.append(
            f"| {model} | {len(played):,} | {round(statistics.mean(scores)):,} "
            f"| {round(statistics.median(scores)):,} | {max(scores):,} "
            f"| {max(game['tile'] for game in played):,} | {reached:.1%} |"
        )
    rows.append(
        f"| Random moves | {baseline['games']:,} | {round(baseline['total'] / baseline['games']):,} "
        f"| | {baseline['best']:,} | | |"
    )
    last = games[-1]
    return "\n".join(rows) + f"\n\nThrough game {last['id']:,}, which ended {last['ended'][:10]}."


def main():
    top = json.loads(fetch("top.json"))
    # recent is newest first, so its head is the last game to finish.
    latest = top["recent"][0]["id"] if top["recent"] else 0
    have = {int(path.stem) for path in GAMES.rglob("*.json")}
    missing = [number for number in range(1, latest + 1) if number not in have]
    for number in missing:
        raw = fetch(f"games/{number}.json")
        path = GAMES / json.loads(raw)["model"] / f"{number}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    if not missing:
        return

    baseline = top["random"]
    (RUN / "baseline.json").write_text(json.dumps(baseline, indent=2) + "\n")
    games = sorted(
        (json.loads(path.read_text()) for path in GAMES.rglob("*.json")),
        key=lambda game: game["id"],
    )
    text = README.read_text()
    head, rest = text.split(START, 1)
    tail = rest.split(END, 1)[1]
    README.write_text(f"{head}{START}\n{results(games, baseline)}\n{END}{tail}")

    first, last = missing[0], missing[-1]
    print(f"Add game {first}" if first == last else f"Add games {first} to {last}")


if __name__ == "__main__":
    main()
