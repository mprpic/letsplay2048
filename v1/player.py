#!/usr/bin/env python3
#
# MIT License
#
# Copyright (c) 2026 Martin Prpič
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
# Plain python3 and the standard library only: sessionbin has no uv, and this
# runs as the user that can read sessionbin's secret key, database and pastes.
"""Play 2048 through OpenRouter's Decisions API, for letsplay2048.lol to show.

Run by letsplay2048.service. Visitors never reach this process: it writes static
JSON that Caddy serves and the page polls.

  live.json        the game in progress, rewritten after every move
  top.json         the leaderboards and averages, rewritten after every game
  games/<id>.json  the replay of every game played, which a daily job in
                   github.com/mprpic/letsplay2048 copies into that repo
"""

import datetime
import http.client
import json
import os
import pathlib
import random
import sys
import time
import urllib.error
import urllib.request

API = "https://openrouter.ai/api/alpha/decisions"
# Played in this order. The published files name a model only once it has
# played, so the page cannot say which one comes next. A model placed ahead
# of one that is part way through its games plays next and splits that run.
# Each new version follows its predecessor, so the pair play days apart.
MODELS = (
    "microsoft/microsoft-decision-1",
    "typesafe/jev-1.13",
    "typesafe/jev-1.14",
    "perplexity/pplx-decider-v1.1-27b",
    "nace-ai/drex-v1.5",
    "nace-ai/drex-v2.0",
    "cloudflare/clef",
    "cloudflare/clef-flash",
    "togethercomputer/tev1-4b-experimental",
    "jaredpalmer/kev-4b",
)
GAMES_EACH = 1000
PACE = 3.0  # seconds from the start of one move to the start of the next
REST = 10.0  # seconds a finished game stays up before the next one starts
TIMEOUT = 30
TOP = 10
RECENT = 5
LIVE_MOVES = 20
BASELINE_GAMES = 1000

# Beside the synced site, not inside it: that sync deletes what it does not manage.
DATA = pathlib.Path(os.environ.get("LETSPLAY2048_DATA", "/var/www/letsplay2048/data"))
GAMES = DATA / "games"
STATE = pathlib.Path(
    os.environ.get(
        "LETSPLAY2048_STATE",
        pathlib.Path.home() / ".local" / "state" / "letsplay2048" / "state.json",
    )
)

# The site tests the model's own judgment, so no advice on playing well and no
# worked-out outcomes belong here. Spelling out the rules alone scored lower:
# 1,694 against this sentence's 2,087, averaged over the first four models.
INSTRUCTIONS = "Which move is best in this game of 2048?"

# Each direction, with the description of it the model is given.
DIRECTIONS = {
    "up": "slide all tiles up",
    "right": "slide all tiles right",
    "down": "slide all tiles down",
    "left": "slide all tiles left",
}


def slide(board, direction):
    """The board after a move and the points it scored, leaving `board` alone."""
    # Every direction is a slide to the left of a transposed or mirrored board.
    rows = board
    if direction in ("up", "down"):
        rows = [list(column) for column in zip(*rows)]
    if direction in ("right", "down"):
        rows = [row[::-1] for row in rows]
    points = 0
    slid = []
    for row in rows:
        tiles = [value for value in row if value]
        merged = []
        while tiles:
            value = tiles.pop(0)
            if tiles and tiles[0] == value:
                tiles.pop(0)
                value *= 2
                points += value
            merged.append(value)
        slid.append(merged + [0] * (4 - len(merged)))
    if direction in ("right", "down"):
        slid = [row[::-1] for row in slid]
    if direction in ("up", "down"):
        slid = [list(column) for column in zip(*slid)]
    return slid, points


def options(board):
    """Each legal move, with the board and points it leads to. Empty once the game is over."""
    results = {direction: slide(board, direction) for direction in DIRECTIONS}
    return {direction: result for direction, result in results.items() if result[0] != board}


def spawn(board):
    """Add a tile to `board` and return it as [row, column, value]."""
    row, column = random.choice([(r, c) for r in range(4) for c in range(4) if not board[r][c]])
    board[row][column] = 2 if random.random() < 0.9 else 4
    return [row, column, board[row][column]]


def new_board():
    board = [[0] * 4 for _ in range(4)]
    spawn(board)
    spawn(board)
    return board


def baseline():
    """Random play's numbers, which the page sets beside each model's."""
    scores = []
    for _ in range(BASELINE_GAMES):
        board, score = new_board(), 0
        while legal := options(board):
            board, points = random.choice(list(legal.values()))
            score += points
            spawn(board)
        scores.append(score)
    return {"games": BASELINE_GAMES, "total": sum(scores), "best": max(scores)}


def request_body(model, board, score, legal):
    """Everything the model is told. The page shows it beside the move it produced."""
    return {
        "model": model,
        "state": {"board": board, "score": score},
        "questions": {
            "move": {
                "type": "choice",
                "instructions": INSTRUCTIONS,
                "criteria": {direction: DIRECTIONS[direction] for direction in legal},
            }
        },
    }


def ask(key, body):
    """The model's pick among the moves it was offered, its probability for each, and the usage."""
    legal = body["questions"]["move"]["criteria"]
    request = urllib.request.Request(
        API,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        reply = json.load(response)
    answer = reply["answers"]["move"]
    if answer["choice"] not in legal:
        raise ValueError(f"not a legal move: {answer['choice']!r}")
    probs = {direction: round(answer["probabilities"][direction], 3) for direction in legal}
    return answer["choice"], probs, reply.get("usage") or {}


def choose(key, game, body):
    """Ask until the model answers, so an outage costs time and never a move."""
    delay = 1
    while True:
        try:
            return ask(key, body)
        except urllib.error.HTTPError as exc:
            # A key that is rejected or out of credit does not recover in minutes.
            delay = 3600 if exc.code in (401, 402, 403) else min(delay * 2, 300)
            try:
                detail = exc.read(300).decode(errors="replace")
            except (OSError, http.client.HTTPException):
                detail = ""
            problem = f"HTTP {exc.code} {detail}"
        # HTTPException is not an OSError: a reply cut short raises IncompleteRead.
        except (OSError, http.client.HTTPException, ValueError, LookupError, TypeError) as exc:
            delay = min(delay * 2, 300)
            problem = repr(exc)
        print(f"no move from {game['model']}, retrying in {delay}s: {problem}", file=sys.stderr)
        write_live(game, "waiting")
        time.sleep(delay)


def write_json(path, value):
    # Write beside the target and rename into place, so a poll never reads half a file.
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(value, separators=(",", ":")))
    tmp.replace(path)


def write_live(game, status):
    write_json(
        DATA / "live.json",
        {
            "status": status,
            "id": game["id"],
            "model": game["model"],
            "board": game["board"],
            "score": game["score"],
            "moves": len(game["log"]),
            "recent": game["log"][-LIVE_MOVES:],
            "request": game["request"],
        },
    )


def write_top(state):
    write_json(DATA / "top.json", {k: state[k] for k in ("all", "recent", "models", "random")})


def ranked(entries):
    # Stable, so of two games with the same score the earlier one stays ahead.
    return sorted(entries, key=lambda entry: -entry["score"])[:TOP]


def finish(state):
    """Record the finished game: its replay, the lists it belongs on, the model's totals."""
    game = state["game"]
    entry = {
        "id": game["id"],
        "model": game["model"],
        "score": game["score"],
        "tile": max(map(max, game["board"])),
        "moves": len(game["log"]),
        "ended": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
    }
    write_json(GAMES / f"{game['id']}.json", {**entry, "start": game["start"], "log": game["log"]})
    stats = state["models"].setdefault(
        game["model"], {"games": 0, "total": 0, "best": 0, "top": []}
    )
    stats["games"] += 1
    stats["total"] += game["score"]
    stats["best"] = max(stats["best"], game["score"])
    stats["top"] = ranked(stats["top"] + [entry])
    state["all"] = ranked(state["all"] + [entry])
    state["recent"] = [entry, *state["recent"]][:RECENT]
    state["game"] = None
    # The state file is the commit: a crash before this line replays the whole
    # function on restart, and one after it leaves only files the next run rewrites.
    write_json(STATE, state)
    write_top(state)
    print(
        f"game {entry['id']} by {entry['model']}: score {entry['score']}, "
        f"{entry['moves']} moves, {game['tokens']} tokens, ${game['cost']:.4f}"
    )


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"count": 0, "game": None, "all": [], "recent": [], "models": {}, "random": baseline()}


def new_game(game_id, model) -> dict:
    board = new_board()
    return {
        "id": game_id,
        "model": model,
        "start": [row[:] for row in board],
        "board": board,
        "score": 0,
        "log": [],
        "request": None,
        "tokens": 0,
        "cost": 0.0,
    }


def main():
    key = os.environ["OPENROUTER_API_KEY"]
    GAMES.mkdir(parents=True, exist_ok=True)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    state = load_state()
    write_top(state)
    if state["game"]:
        write_live(state["game"], "playing")
    due = time.monotonic()
    while True:
        game = state["game"]
        if game is None:
            played = {model: stats["games"] for model, stats in state["models"].items()}
            model = next((m for m in MODELS if played.get(m, 0) < GAMES_EACH), None)
            if model is None:
                print("every model has played its share")
                return 0
            state["count"] += 1
            game = state["game"] = new_game(state["count"], model)
            write_json(STATE, state)
            write_live(game, "playing")
            due = time.monotonic() + PACE
        legal = options(game["board"])
        if not legal:
            write_live(game, "over")
            finish(state)
            time.sleep(REST)
            continue
        time.sleep(max(0.0, due - time.monotonic()))
        due = time.monotonic() + PACE
        if len(legal) == 1:
            # Nothing to decide, so nothing to pay for.
            direction, probs, game["request"] = next(iter(legal)), None, None
        else:
            body = request_body(game["model"], game["board"], game["score"], legal)
            direction, probs, usage = choose(key, game, body)
            game["request"] = body
            # Only the journal line reads these, so a reply without them still counts.
            game["tokens"] += usage.get("input_tokens") or 0
            game["cost"] += usage.get("cost") or 0.0
        game["board"], points = legal[direction]
        game["score"] += points
        game["log"].append(
            {
                "seq": len(game["log"]) + 1,
                "dir": direction,
                "probs": probs,
                "tile": spawn(game["board"]),
            }
        )
        write_json(STATE, state)
        write_live(game, "playing")


if __name__ == "__main__":
    sys.exit(main())
