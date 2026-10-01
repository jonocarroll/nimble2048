# nimble2048

2048 played by the local `nimble` classifier, with a live web visualiser.

Run:  `uv run play.py [--port 8048] [--delay 0.15] [--model nimble]`

Open: http://localhost:8048

![](game.png)

Ollama supports a [Jev-style classifier](https://ollama.com/blog/ollama-now-supports-jev-style-decision-models) and can be called as an API locally. This script sends a representation of the current board and some gameplay instructions to the `nimble` model (0-shot each move) and recieves a classification of moves and their probabilities. The highest legal move is then played.

Built with Claude Code.