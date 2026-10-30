# letsplay2048

Every game played on [letsplay2048.lol](https://letsplay2048.lol), where models on OpenRouter's
Decisions API play 2048 live, one move every three seconds. This repo holds the code that plays them
and a copy of each finished game, refreshed daily.

## How a game is played

The player, [`v1/player.py`](v1/player.py), plays one game at a time. Before each move it works out
which of the four moves would change the board. If none would, the game is over. If only one would,
it plays that move without asking the model. Otherwise it sends the board, the score and the moves
that are possible to `https://openrouter.ai/api/alpha/decisions`:

```json
{
  "model": "typesafe/jev-1.13",
  "state": {
    "board": [[0, 0, 0, 0], [0, 0, 0, 0], [4, 0, 0, 0], [2, 0, 0, 0]],
    "score": 0
  },
  "questions": {
    "move": {
      "type": "choice",
      "instructions": "Which move is best in this game of 2048?",
      "criteria": {
        "up": "slide all tiles up",
        "right": "slide all tiles right"
      }
    }
  }
}
```

The reply names one of the moves offered and gives a probability for each. The named move is
played, then a 2 (nine times in ten) or a 4 appears in a random empty cell, as in the original game.

That request is everything the model gets: no rules, no strategy and no memory of earlier moves.
The one-line instruction was chosen over a version that also spelled out the rules, which averaged
1,694 points against its 2,087 across the first four models tried.

A merge scores the value of the tile it makes, and a game goes on past 2048 until no move changes
the board. A failed call (a timeout, a rate limit, a reply naming a move that was not offered) is
retried until one succeeds, so an outage delays a game but never decides a move.

## Runs

`v1` is one fixed setup: the request above, with the moves always offered in the order up, right,
down, left, and these ten models, each playing 1,000 games before the next one starts:

1. microsoft/microsoft-decision-1
2. typesafe/jev-1.13
3. typesafe/jev-1.14
4. nace-ai/drex-v1.5
5. nace-ai/drex-v2.0
6. cloudflare/clef
7. cloudflare/clef-flash
8. togethercomputer/tev1-4b-experimental
9. jaredpalmer/kev-4b
10. perplexity/pplx-decider-v1.1-27b

A change to what the models are shown, or to how their games are dealt, will start `v2` in its
own directory, so games from different setups are never mixed.

Random play is the baseline: before v1 started, the player played 1,000 games choosing uniformly
among the moves that change the board. Its totals are in [`v1/baseline.json`](v1/baseline.json).

## Results

<!-- results:start -->
| Model | Games | Average | Median | Best | Highest tile | Reached 512 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| microsoft/microsoft-decision-1 | 1,000 | 2,289 | 2,102 | 8,888 | 512 | 6.1% |
| typesafe/jev-1.13 | 1,000 | 2,572 | 2,346 | 8,312 | 512 | 8.4% |
| typesafe/jev-1.14 | 1,000 | 790 | 692 | 2,776 | 256 | 0.0% |
| Random moves | 1,000 | 1,100 | | 3,184 | | |

Through game 3,000, which ended 2026-10-29.
<!-- results:end -->

The last model listed is usually still playing its 1,000 games.

## Data

`v1/games/<model>/<id>.json` holds one finished game. Ids count every game in the run in the order
it was played. Game 1185, one of jev-1.13's best, cut to its first two moves:

```json
{
  "id": 1185,
  "model": "typesafe/jev-1.13",
  "score": 8312,
  "tile": 512,
  "moves": 565,
  "ended": "2026-10-19T18:38:48+00:00",
  "start": [[0, 0, 0, 0], [0, 0, 0, 0], [4, 0, 0, 0], [2, 0, 0, 0]],
  "log": [
    {"seq": 1, "dir": "up", "probs": {"up": 0.67, "right": 0.33}, "tile": [0, 2, 2]},
    {"seq": 2, "dir": "left", "probs": {"right": 0.17, "down": 0.16, "left": 0.67}, "tile": [2, 3, 2]}
  ]
}
```

| Field | Meaning |
| --- | --- |
| `score`, `tile`, `moves` | Final score, highest tile, and moves played, counting moves the model was not asked about |
| `ended` | When the game ended, in UTC |
| `start` | The board before the first move, row by row from the top, with 0 for an empty cell |
| `log[].dir` | The move played |
| `log[].probs` | The probability the model gave each move it was offered, rounded to three places. `null` when only one move was possible and the model was not asked |
| `log[].tile` | The tile that appeared after the move, as `[row, column, value]` |

Nothing in a game is left to chance after the fact: any board in it can be rebuilt by starting from
`start` and, for each entry in `log`, sliding as `slide()` in `player.py` does, then placing `tile`.

## Caveats

- **Games are not seeded**, so each model plays different boards. Over 1,000 games that averages
  out, but it makes any one game a poor comparison.
- **The Decisions API is in alpha.** The request sets no sampling parameters, so each model runs at
  its provider's defaults, and the provider behind a model can change during a run.
- The games do not record what each call cost or how many tokens it used.

## License

The code, `v1/player.py` and `scripts/`, is under the [MIT License](LICENSE). The game data,
`v1/games/` and `v1/baseline.json`, is dedicated to the public domain under
[CC0 1.0](LICENSE-DATA).
