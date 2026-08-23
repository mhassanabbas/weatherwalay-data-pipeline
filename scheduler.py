# ═══════════════════════════════════════════════════════
# scheduler.py — UNCHANGED
# Runs mongo.py then interpolate.py automatically every hour.
# Start this ONCE (python scheduler.py) and leave the terminal
# open — never restart it. Do not run mongo.py/interpolate.py
# manually anymore once this is running; scheduler.py owns that.
# ═══════════════════════════════════════════════════════
import schedule
import time
import subprocess
import sys
from datetime import datetime
from error_logger import log_error, log_success

RUN_EVERY_HOURS = 1
PYTHON          = sys.executable


def run_script(script_name):
    print(f"  Running {script_name}...")
    start = time.time()
    try:
        result = subprocess.run([PYTHON, script_name], capture_output=True, text=True, timeout=1200)
        elapsed = round(time.time() - start, 2)

        if result.returncode == 0:
            print(f"  {script_name} finished in {elapsed}s")
            log_success(script_name, "scheduler_run", extra={"elapsed": elapsed})
            return True
        else:
            error_msg = result.stderr[-500:] if result.stderr else "Unknown error"
            print(f"  {script_name} FAILED: {error_msg}")
            log_error(script_name, "scheduler_run", "ScriptFailed", error_msg,
                      extra={"returncode": result.returncode, "elapsed": elapsed})
            return False

    except subprocess.TimeoutExpired:
        print(f"  {script_name} TIMED OUT (>20 minutes)")
        log_error(script_name, "scheduler_run", "TimeoutError", "Script exceeded 20 minutes")
        return False

    except Exception as e:
        print(f"  {script_name} ERROR: {e}")
        log_error(script_name, "scheduler_run", type(e).__name__, str(e))
        return False


def run_pipeline():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("\n" + "="*50)
    print(f"PIPELINE RUN - {now}")
    print("="*50)

    if not run_script("mongo.py"):
        print("Pipeline stopped - mongo.py failed")
        log_error("scheduler", "pipeline", "PipelineFailed", "Stopped at mongo.py")
        return

    if not run_script("interpolate.py"):
        print("Pipeline stopped - interpolate.py failed")
        log_error("scheduler", "pipeline", "PipelineFailed", "Stopped at interpolate.py")
        return

    print(f"\nPipeline complete - {datetime.now().strftime('%H:%M:%S')}")
    print("API is now serving fresh data.")
    log_success("scheduler", "full_pipeline")


def main():
    print("="*50)
    print("WEATHER PIPELINE SCHEDULER")
    print(f"Runs every {RUN_EVERY_HOURS} hour(s) automatically")
    print("Press Ctrl+C to stop")
    print("="*50)

    print("\nRunning pipeline immediately on startup...")
    run_pipeline()

    schedule.every(RUN_EVERY_HOURS).hours.do(run_pipeline)
    print(f"\nScheduler running - next run in {RUN_EVERY_HOURS} hour(s)")

    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()