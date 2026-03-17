from flask import Flask, jsonify, request
import threading
import time
import gc

app = Flask(__name__)

state_lock = threading.Lock()
memory_holder = []
cpu_jobs_started = 0
memory_jobs_started = 0


def _to_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

@app.route("/")
def home():
    return jsonify({"message": "AIOps Stress Test Service Running"})


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


@app.route("/stats")
def stats():
    with state_lock:
        return jsonify({
            "memory_items": len(memory_holder),
            "cpu_jobs_started": cpu_jobs_started,
            "memory_jobs_started": memory_jobs_started,
            "active_threads": threading.active_count()
        })


# CPU Stress
@app.route("/cpu-stress")
def cpu_stress():
    workers = max(1, min(_to_int(request.args.get("workers"), 1), 8))
    iterations = max(10_000, min(_to_int(request.args.get("iterations"), 10**8), 2 * 10**8))

    def stress(loop_count):
        x = 1
        for _ in range(loop_count):
            x = (x * x + 1) % 1_000_000_007

    started = []
    for _ in range(workers):
        thread = threading.Thread(target=stress, args=(iterations,), daemon=True)
        thread.start()
        started.append(thread.name)

    global cpu_jobs_started
    with state_lock:
        cpu_jobs_started += workers

    return jsonify({
        "status": "CPU stress started",
        "workers": workers,
        "iterations": iterations,
        "threads": started
    })


@app.route("/memory-leak")
def memory_leak():
    batches = max(1, min(_to_int(request.args.get("batches"), 50), 300))
    chunk_size = max(1_000, min(_to_int(request.args.get("chunk_size"), 200_000), 500_000))
    sleep_ms = max(0, min(_to_int(request.args.get("sleep_ms"), 200), 5000))

    def leak():
        for _ in range(batches):
            with state_lock:
                memory_holder.extend(["AIOpsMemoryLeak"] * chunk_size)
            time.sleep(sleep_ms / 1000.0)

    thread = threading.Thread(target=leak, daemon=True)
    thread.start()

    global memory_jobs_started
    with state_lock:
        memory_jobs_started += 1

    return jsonify({
        "status": "Memory leak simulation started",
        "batches": batches,
        "chunk_size": chunk_size,
        "sleep_ms": sleep_ms,
        "thread": thread.name
    })


# Reset Memory
@app.route("/reset-memory")
def reset_memory():
    global memory_holder
    with state_lock:
        memory_holder = []
    gc.collect()
    return jsonify({"status": "memory cleared", "memory_items": 0})


# Error Simulation
@app.route("/error")
def error():
    raise Exception("Simulated application error")


@app.route("/crash")
def crash():
    import os
    os._exit(1)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)