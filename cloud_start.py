"""
Cloud Entry Point — production-grade 24/7 supervisor
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Designed for Railway, Fly.io, Render, or any container host.

Responsibilities:
  • Start FastAPI web server on $PORT immediately so the platform's
    healthcheck passes during cold start.
  • Spawn the trading bot in a daemon thread AFTER the web server
    is reachable. If the bot crashes, the supervisor restarts it
    with exponential backoff (capped) — never kills the web server.
  • Expose /health endpoint with deep health metrics:
        - web_uptime_sec
        - bot_running
        - bot_uptime_sec
        - bot_restart_count
        - last_bot_error
        - learning_engine_summary
  • Emit periodic "alive" log lines so log-based monitors can detect
    silent failures (Railway, Better Stack, UptimeRobot, etc).
  • Handle SIGTERM gracefully so the platform's rolling deploys don't
    leave half-completed orders.
"""
from __future__ import annotations

import os
import sys
import time
import signal
import threading
import traceback
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Supervisor state (process-wide, exposed via /health) ──────────────────────
_STATE = {
    "started_at": time.time(),
    "bot_started_at": None,
    "bot_running": False,
    "bot_restart_count": 0,
    "bot_last_error": None,
    "bot_last_heartbeat": None,
    "shutdown_requested": False,
}
_STATE_LOCK = threading.Lock()


def _set(**kwargs) -> None:
    with _STATE_LOCK:
        _STATE.update(kwargs)


def _snap() -> dict:
    with _STATE_LOCK:
        snap = dict(_STATE)
    snap["web_uptime_sec"] = int(time.time() - snap["started_at"])
    if snap.get("bot_started_at"):
        snap["bot_uptime_sec"] = int(time.time() - snap["bot_started_at"])
    if snap.get("bot_last_heartbeat"):
        snap["bot_heartbeat_age_sec"] = int(time.time() - snap["bot_last_heartbeat"])
    return snap


# ── Health endpoint installation (mounted onto FastAPI app) ───────────────────

def _install_health_endpoints(app) -> None:
    """
    Mount /health, /readyz, /livez, /metrics on the FastAPI app.
    Idempotent — safe to call multiple times.
    """
    from fastapi import status
    from fastapi.responses import JSONResponse, PlainTextResponse

    seen_routes = {r.path for r in app.routes if hasattr(r, "path")}

    if "/health" not in seen_routes:
        @app.get("/health", include_in_schema=False)
        async def health():
            snap = _snap()
            # Deep health: include learning engine summary if available
            try:
                from core.learning import get_engine
                snap["learning"] = get_engine().get_summary()
            except Exception:
                snap["learning"] = None
            return JSONResponse(snap)

    if "/livez" not in seen_routes:
        @app.get("/livez", include_in_schema=False)
        async def livez():
            return PlainTextResponse("ok")

    if "/readyz" not in seen_routes:
        @app.get("/readyz", include_in_schema=False)
        async def readyz():
            snap = _snap()
            ready = snap["web_uptime_sec"] > 2
            return JSONResponse(
                {"ready": ready, **snap},
                status_code=status.HTTP_200_OK if ready
                else status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    if "/metrics" not in seen_routes:
        @app.get("/metrics", include_in_schema=False)
        async def metrics():
            """Prometheus-compatible plaintext metrics."""
            s = _snap()
            lines = [
                "# HELP ferchaud_web_uptime_seconds Web server uptime",
                "# TYPE ferchaud_web_uptime_seconds counter",
                f"ferchaud_web_uptime_seconds {s['web_uptime_sec']}",
                "# HELP ferchaud_bot_running 1 if trading bot is running",
                "# TYPE ferchaud_bot_running gauge",
                f"ferchaud_bot_running {1 if s['bot_running'] else 0}",
                "# HELP ferchaud_bot_restart_count Total bot restarts",
                "# TYPE ferchaud_bot_restart_count counter",
                f"ferchaud_bot_restart_count {s['bot_restart_count']}",
            ]
            if s.get("bot_uptime_sec"):
                lines += [
                    "# HELP ferchaud_bot_uptime_seconds Bot uptime since last (re)start",
                    "# TYPE ferchaud_bot_uptime_seconds counter",
                    f"ferchaud_bot_uptime_seconds {s['bot_uptime_sec']}",
                ]
            if s.get("bot_heartbeat_age_sec") is not None:
                lines += [
                    "# HELP ferchaud_bot_heartbeat_age_seconds Seconds since last bot heartbeat",
                    "# TYPE ferchaud_bot_heartbeat_age_seconds gauge",
                    f"ferchaud_bot_heartbeat_age_seconds {s['bot_heartbeat_age_sec']}",
                ]
            return PlainTextResponse("\n".join(lines) + "\n",
                                     media_type="text/plain; version=0.0.4")


# ── Bot supervisor (auto-restart with backoff) ────────────────────────────────

def _bot_supervisor() -> None:
    """
    Run main.main() in this thread. If it raises, log + restart with
    exponential backoff capped at 5 minutes. Never propagates exceptions.

    Heartbeat: writes _STATE["bot_last_heartbeat"] = now from inside the
    bot loop via a hook, so external monitors can detect silent stalls.
    """
    backoff = 5.0          # initial 5s
    max_backoff = 300.0    # cap at 5 min

    # Wait briefly so the web server can bind and answer healthcheck
    time.sleep(8)

    while not _STATE["shutdown_requested"]:
        _set(
            bot_started_at=time.time(),
            bot_running=True,
            bot_last_error=None,
        )
        try:
            print(f"[CLOUD] Trading bot starting (restart #{_STATE['bot_restart_count']})",
                  flush=True)
            # Inject heartbeat hook into main module
            import importlib
            try:
                main_mod = importlib.import_module("main")
                # Attach a heartbeat callable that the loop can call
                setattr(main_mod, "__heartbeat__", lambda: _set(
                    bot_last_heartbeat=time.time()
                ))
            except Exception as e:
                print(f"[CLOUD] heartbeat hook install failed: {e}", flush=True)
                main_mod = None

            from main import main as bot_main
            bot_main()
            # Clean exit (rare — main() runs forever)
            print("[CLOUD] Bot main() returned — shutting down", flush=True)
            _set(bot_running=False)
            return

        except SystemExit as e:
            print(f"[CLOUD] Bot SystemExit({e.code}) — not restarting", flush=True)
            _set(bot_running=False, bot_last_error=f"SystemExit({e.code})")
            return

        except KeyboardInterrupt:
            print("[CLOUD] Bot interrupted — shutting down", flush=True)
            _set(bot_running=False)
            return

        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            print(f"[CLOUD] Bot CRASHED: {err}", flush=True)
            traceback.print_exc()
            _set(
                bot_running=False,
                bot_last_error=err,
                bot_restart_count=_STATE["bot_restart_count"] + 1,
            )

        if _STATE["shutdown_requested"]:
            return

        sleep_for = min(max_backoff, backoff)
        print(f"[CLOUD] Restarting bot in {sleep_for:.0f}s "
              f"(restart #{_STATE['bot_restart_count']})", flush=True)
        # Sleep in 1s steps so SIGTERM can interrupt
        slept = 0.0
        while slept < sleep_for and not _STATE["shutdown_requested"]:
            time.sleep(1.0)
            slept += 1.0
        backoff = min(max_backoff, backoff * 1.7)


# ── Periodic alive logger (log-based monitors hook into this) ─────────────────

def _alive_logger() -> None:
    interval = int(os.getenv("ALIVE_LOG_INTERVAL_SEC", "300"))  # 5 min default
    while not _STATE["shutdown_requested"]:
        time.sleep(interval)
        if _STATE["shutdown_requested"]:
            break
        s = _snap()
        print(
            f"[ALIVE] web_up={s['web_uptime_sec']}s "
            f"bot_running={s['bot_running']} "
            f"bot_up={s.get('bot_uptime_sec', 0)}s "
            f"restarts={s['bot_restart_count']} "
            f"hb_age={s.get('bot_heartbeat_age_sec', 'n/a')}s "
            f"ts={datetime.utcnow().isoformat()}Z",
            flush=True,
        )


# ── Signal handling ───────────────────────────────────────────────────────────

def _install_signal_handlers() -> None:
    def handler(signum, frame):
        sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        print(f"[CLOUD] Received {sig_name} — graceful shutdown", flush=True)
        _set(shutdown_requested=True)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    port = int(os.getenv("PORT", "3000"))
    mode = os.getenv("TRADING_MODE", "paper")
    print(f"[CLOUD] Ferchaud booting on port {port} (mode={mode})", flush=True)
    print(f"[CLOUD] Python {sys.version_info.major}.{sys.version_info.minor} "
          f"@ {os.uname().nodename if hasattr(os, 'uname') else 'unknown'}",
          flush=True)

    _install_signal_handlers()

    # Pre-import learning engine so DB is ready before first request
    try:
        from core.learning import get_engine
        get_engine()
    except Exception as e:
        print(f"[CLOUD] LearningEngine pre-init warn: {e}", flush=True)

    # Mount health endpoints onto the existing FastAPI app
    try:
        from web.server import app as web_app
        _install_health_endpoints(web_app)
        print("[CLOUD] Health endpoints mounted: /health /livez /readyz /metrics",
              flush=True)
    except Exception as e:
        print(f"[CLOUD] Could not mount health endpoints: {e}", flush=True)
        traceback.print_exc()

    # Spawn supervisors
    bot_thread = threading.Thread(
        target=_bot_supervisor, daemon=True, name="BotSupervisor"
    )
    bot_thread.start()

    alive_thread = threading.Thread(
        target=_alive_logger, daemon=True, name="AliveLogger"
    )
    alive_thread.start()

    # Block on uvicorn (main thread)
    import uvicorn

    config = uvicorn.Config(
        "web.server:app",
        host="0.0.0.0",
        port=port,
        log_level=os.getenv("LOG_LEVEL", "info"),
        access_log=os.getenv("ACCESS_LOG", "false").lower() in ("1", "true", "yes"),
        timeout_keep_alive=30,
        # Increase the default 64 KB max request line for cookies/long URLs
        h11_max_incomplete_event_size=65536 * 4,
    )
    server = uvicorn.Server(config)

    try:
        server.run()
    except Exception as e:
        print(f"[CLOUD] uvicorn crashed: {e}", flush=True)
        traceback.print_exc()
        raise
    finally:
        _set(shutdown_requested=True)
        print("[CLOUD] Shutdown complete", flush=True)


if __name__ == "__main__":
    main()
