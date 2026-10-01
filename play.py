#!/usr/bin/env python3
"""2048 played by the local `nimble` classifier, with a live web visualiser.

Run:  uv run play.py [--port 8048] [--delay 0.15] [--model nimble]
Open: http://localhost:8048
"""
import argparse
import json
import random
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SIZE = 4
MOVES = ("up", "down", "left", "right")


# ---------- game logic ----------
def slide_row_left(row):
    tiles = [v for v in row if v]
    out, i = [], 0
    while i < len(tiles):
        if i + 1 < len(tiles) and tiles[i] == tiles[i + 1]:
            out.append(tiles[i] + 1)  # exponents: merging n,n -> n+1
            i += 2
        else:
            out.append(tiles[i])
            i += 1
    return out + [0] * (SIZE - len(out))


def apply_move(board, move):
    """Board holds exponents (0 = empty, n = 2^n). Returns new board."""
    b = [r[:] for r in board]
    if move in ("up", "down"):
        b = [list(c) for c in zip(*b)]
    if move in ("right", "down"):
        b = [r[::-1] for r in b]
    b = [slide_row_left(r) for r in b]
    if move in ("right", "down"):
        b = [r[::-1] for r in b]
    if move in ("up", "down"):
        b = [list(c) for c in zip(*b)]
    return b


def legal_moves(board):
    return [m for m in MOVES if apply_move(board, m) != board]


def spawn(board):
    empty = [(r, c) for r in range(SIZE) for c in range(SIZE) if not board[r][c]]
    r, c = random.choice(empty)
    board[r][c] = 1 if random.random() < 0.9 else 2


def new_board():
    b = [[0] * SIZE for _ in range(SIZE)]
    spawn(b)
    spawn(b)
    return b


# ---------- classifier ----------
def describe(board, legal):
    rows = "\n".join(" ".join(str(v) for v in r) for r in board)
    return (
        "2048 board, 4x4. Each number n is a tile of value 2^n (0 = empty). "
        "Merging two equal numbers makes n+1.\n" + rows +
        "\nLegal moves: " + ", ".join(legal)
    )


INSTRUCTIONS = (
    "Pick the best 2048 move. Strategy, in priority order: "
    "1) Anchor the largest tile in the bottom-left corner and never move it out; "
    "favour down and left, use right only when needed, and use up only as a last resort. "
    "2) Keep the bottom row full and ordered, biggest to smallest from left to right, "
    "then snake upward: the next row runs smallest to biggest, and so on. "
    "3) Prefer moves that merge equal tiles, especially large ones, and moves that set up "
    "a merge next turn (equal numbers adjacent). "
    "4) Keep as many empty cells as possible and avoid isolating a small tile between "
    "two much larger ones. "
    "5) Avoid moves that leave the board with no empty cells or only one legal move next turn."
)


def ask_classifier(url, model, board, legal):
    body = {
        "model": model,
        "state": describe(board, legal),
        "questions": {
            "move": {
                "type": "choice",
                "instructions": INSTRUCTIONS,
                "criteria": {m: None for m in legal},
            }
        },
    }
    req = urllib.request.Request(url, data=json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=60) as resp:
        ans = json.load(resp)["answers"]["move"]
    probs = ans.get("probabilities") or {}
    ranked = sorted(legal, key=lambda m: probs.get(m, 0), reverse=True)
    choice = ans.get("choice") if ans.get("choice") in legal else ranked[0]
    return choice, ans.get("confidence"), probs


# ---------- shared state + loop ----------
def fresh_state():
    return {
        "board": new_board(),
        "turns": 0,
        "max_tile": 2,
        "last_move": None,
        "confidence": None,
        "probs": {},
        "legal": [],
        "status": "playing",  # playing | over | error
        "message": "",
        "gen": 0,
    }


state = fresh_state()
lock = threading.Lock()


def restart():
    with lock:
        gen = state["gen"] + 1
        state.clear()
        state.update(fresh_state())
        state["gen"] = gen


def snapshot():
    with lock:
        s = dict(state)
        s["board"] = [r[:] for r in state["board"]]
        return s


def play_loop(args):
    import time

    url = f"http://localhost:{args.ollama_port}/v1/systemone"
    while True:
        with lock:
            board = [r[:] for r in state["board"]]
            gen, status = state["gen"], state["status"]
        if status != "playing":  # finished: idle until "start over"
            time.sleep(0.1)
            continue
        legal = legal_moves(board)
        if not legal:
            with lock:
                if state["gen"] == gen:
                    state["status"] = "over"
                    state["message"] = "No legal moves left."
                    state["legal"] = []
            continue
        try:
            if len(legal) == 1:
                move, conf, probs = legal[0], None, {legal[0]: 1.0}
            else:
                move, conf, probs = ask_classifier(url, args.model, board, legal)
        except Exception as e:  # classifier unreachable etc. -> stop gracefully
            with lock:
                if state["gen"] == gen:
                    state["status"] = "error"
                    state["message"] = f"Classifier error: {e}"
            continue
        board = apply_move(board, move)
        spawn(board)
        with lock:
            if state["gen"] != gen:  # restarted while the classifier was thinking
                continue
            state["board"] = board
            state["turns"] += 1
            state["max_tile"] = 2 ** max(max(r) for r in board)
            state["last_move"] = move
            state["confidence"] = conf
            state["probs"] = probs
            state["legal"] = legal
        time.sleep(args.delay)


# ---------- web ----------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/state":
            data, ctype = json.dumps(snapshot()).encode(), "application/json"
        elif self.path in ("/", "/index.html"):
            data, ctype = PAGE.encode(), "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


    def do_POST(self):
        if self.path == "/restart":
            restart()
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>2048 × nimble</title>
<style>
  body{font-family:system-ui,sans-serif;background:#faf8ef;color:#776e65;display:flex;flex-direction:column;align-items:center;margin:2rem}
  .stats{display:flex;gap:1rem;margin-bottom:1rem}
  .stat{background:#bbada0;color:#fff;border-radius:6px;padding:.5rem 1.2rem;text-align:center;min-width:5.5rem}
  .stat b{display:block;font-size:1.6rem}.stat span{font-size:.7rem;text-transform:uppercase;opacity:.85}
  #grid{display:grid;grid-template-columns:repeat(4,100px);grid-gap:10px;background:#bbada0;padding:10px;border-radius:8px;position:relative}
  .t{width:100px;height:100px;border-radius:4px;background:#cdc1b4;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:2.2rem;transition:background .1s}
  #msg{margin-top:1rem;min-height:1.5rem;font-weight:600}
  #mv{margin-top:.3rem;font-size:.85rem;opacity:.8}
  .main{display:flex;gap:2rem;align-items:flex-start;flex-wrap:wrap;justify-content:center}
  #moves{width:260px;display:flex;flex-direction:column;gap:8px}
  .mrow{position:relative;border-radius:6px;padding:.5rem .7rem;background:#eee4da;border:3px solid transparent;overflow:hidden}
  .mrow .bar{position:absolute;left:0;top:0;bottom:0;background:#f2b179;opacity:.55;width:0;transition:width .15s}
  .mrow .lbl{position:relative;display:flex;justify-content:space-between;font-weight:600}
  .mrow.sel{border-color:#776e65;background:#f9e3c0}
  .mrow.illegal{opacity:.4}
  button{margin-top:1rem;font:inherit;font-weight:700;background:#8f7a66;color:#fff;border:0;border-radius:6px;padding:.6rem 1.4rem;cursor:pointer}
  button:hover{background:#9f8a76}
</style></head><body>
<h1>2048 × nimble</h1>
<div class="stats">
  <div class="stat"><b id="max">2</b><span>Highest tile</span></div>
  <div class="stat"><b id="turns">0</b><span>Turns</span></div>
</div>
<div class="main">
  <div id="grid"></div>
  <div id="moves"></div>
</div>
<button id="restart">Start over</button>
<div id="mv"></div><div id="msg"></div>
<script>
const colors={2:'#eee4da',4:'#ede0c8',8:'#f2b179',16:'#f59563',32:'#f67c5f',64:'#f65e3b',128:'#edcf72',256:'#edcc61',512:'#edc850',1024:'#edc53f',2048:'#edc22e'};
const grid=document.getElementById('grid');
const ARROW={up:'↑',down:'↓',left:'←',right:'→'};
const mrows={};
for(const m of ['up','down','left','right']){
  const r=document.createElement('div');r.className='mrow';
  r.innerHTML='<div class="bar"></div><div class="lbl"><span>'+ARROW[m]+' '+m+'</span><span class="pct">–</span></div>';
  document.getElementById('moves').appendChild(r);mrows[m]=r;
}
document.getElementById('restart').onclick=()=>fetch('/restart',{method:'POST'});
for(let i=0;i<16;i++){const d=document.createElement('div');d.className='t';grid.appendChild(d);}
async function tick(){
  try{
    const s=await (await fetch('/state')).json();
    s.board.flat().forEach((e,i)=>{
      const d=grid.children[i],v=e?2**e:0;
      d.textContent=v||'';
      d.style.background=v?(colors[v]||'#3c3a32'):'#cdc1b4';
      d.style.color=v>4?'#f9f6f2':'#776e65';
      d.style.fontSize=v>=1024?'1.6rem':v>=128?'1.9rem':'2.2rem';
    });
    for(const m in mrows){
      const r=mrows[m],p=s.probs[m],illegal=s.legal.length>0&&!s.legal.includes(m);
      r.classList.toggle('sel',m===s.last_move);
      r.classList.toggle('illegal',illegal);
      r.querySelector('.bar').style.width=(p!=null?p*100:0)+'%';
      r.querySelector('.pct').textContent=p!=null?(p*100).toFixed(1)+'%':(illegal?'illegal':'–');
    }
    max.textContent=s.max_tile;turns.textContent=s.turns;
    mv.textContent=s.last_move?`last move: ${s.last_move}`+(s.confidence!=null?` (confidence ${s.confidence.toFixed(2)})`:''):'';
    msg.textContent=s.status==='over'?'Game over — '+s.message:s.status==='error'?s.message:'';
  }catch(e){msg.textContent='Waiting for server…';}
  setTimeout(tick,100);
}
tick();
</script></body></html>
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8048)
    p.add_argument("--ollama-port", type=int, default=11434)
    p.add_argument("--model", default="nimble")
    p.add_argument("--delay", type=float, default=0.15, help="seconds between moves")
    args = p.parse_args()

    threading.Thread(target=play_loop, args=(args,), daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Visualiser: http://localhost:{args.port}  (Ctrl-C to quit)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
