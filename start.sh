#!/bin/bash
# AlgoTrader Pro — unified launcher
# Usage:
#   ./start.sh           start both servers (foreground, Ctrl+C to stop)
#   ./start.sh --daemon  start in background (logs to logs/)
#   ./start.sh --stop    stop background processes
#   ./start.sh --status  show running status

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGS="$ROOT/logs"
PIDFILE_B="$LOGS/backend.pid"
PIDFILE_F="$LOGS/frontend.pid"

mkdir -p "$LOGS"

case "$1" in

# ── Stop ─────────────────────────────────────────────────────────────────────
--stop)
  echo "Stopping AlgoTrader..."
  for pf in "$PIDFILE_B" "$PIDFILE_F"; do
    if [ -f "$pf" ]; then
      pid=$(cat "$pf")
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" && echo "  Stopped PID $pid"
      fi
      rm -f "$pf"
    fi
  done
  echo "Done."
  exit 0
  ;;

# ── Status ───────────────────────────────────────────────────────────────────
--status)
  for label pf in "Backend" "$PIDFILE_B" "Frontend" "$PIDFILE_F"; do
    true  # handled below
  done
  for entry in "Backend:$PIDFILE_B" "Frontend:$PIDFILE_F"; do
    label="${entry%%:*}"
    pf="${entry##*:}"
    if [ -f "$pf" ]; then
      pid=$(cat "$pf")
      if kill -0 "$pid" 2>/dev/null; then
        echo "  $label  RUNNING  (PID $pid)"
      else
        echo "  $label  DEAD     (stale PID $pid)"
      fi
    else
      echo "  $label  STOPPED"
    fi
  done
  exit 0
  ;;

# ── Daemon ───────────────────────────────────────────────────────────────────
--daemon)
  echo "Starting AlgoTrader in background..."

  # Backend
  cd "$ROOT/backend"
  [ ! -d ".venv" ] && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -q
  nohup .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 \
        >> "$LOGS/backend.log" 2>&1 &
  echo $! > "$PIDFILE_B"
  echo "  Backend  PID $(cat $PIDFILE_B)  → http://localhost:8000"

  # Frontend
  cd "$ROOT/frontend"
  [ ! -d "node_modules" ] && npm install -q
  nohup npm run start \
        >> "$LOGS/frontend.log" 2>&1 &
  echo $! > "$PIDFILE_F"
  echo "  Frontend PID $(cat $PIDFILE_F) → http://localhost:3000"

  echo ""
  echo "Logs:  tail -f $LOGS/backend.log"
  echo "       tail -f $LOGS/frontend.log"
  echo "Stop:  ./start.sh --stop"
  exit 0
  ;;

# ── Foreground (default) ──────────────────────────────────────────────────────
*)
  echo "Starting AlgoTrader Pro..."

  cd "$ROOT/backend"
  if [ ! -d ".venv" ]; then
    echo "  Creating Python venv..."
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt -q
  fi
  .venv/bin/uvicorn main:app --host 0.0.0.0 --reload --port 8000 &
  BACKEND_PID=$!

  cd "$ROOT/frontend"
  [ ! -d "node_modules" ] && npm install -q
  npm run dev &
  FRONTEND_PID=$!

  echo ""
  echo "  Dashboard → http://localhost:3000"
  echo "  API Docs  → http://localhost:8000/docs"
  echo "  Ctrl+C to stop both."
  echo ""

  trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; echo 'Stopped.'" EXIT INT TERM
  wait
  ;;
esac
