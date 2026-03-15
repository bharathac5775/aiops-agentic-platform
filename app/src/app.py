from flask import Flask, jsonify
import threading
import time

app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({"message": "AIOps Stress Test Service Running"})


@app.route("/health")
def health():
    return jsonify({"status": "healthy"})


# CPU Stress
@app.route("/cpu-stress")
def cpu_stress():
    def stress():
        x = 0
        for _ in range(10**8):
            x = x * x + 1

    thread = threading.Thread(target=stress)
    thread.start()

    return jsonify({"status": "CPU stress started"})


# Controlled Memory Leak
memory_holder = []

@app.route("/memory-leak")
def memory_leak():
    def leak():
        for _ in range(50):
            memory_holder.extend(["AIOpsMemoryLeak"] * 200000)
            time.sleep(0.2)

    thread = threading.Thread(target=leak)
    thread.start()

    return jsonify({"status": "Memory leak simulation started"})


# Reset Memory
@app.route("/reset-memory")
def reset_memory():
    global memory_holder
    memory_holder = []
    return jsonify({"status": "memory cleared"})


# Error Simulation
@app.route("/error")
def error():
    raise Exception("Simulated application error")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)