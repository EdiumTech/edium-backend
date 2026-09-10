"""Trusted host adapter. Candidate Python is interpreted ONLY inside WASM.

The host never calls eval, exec, or compile on candidate text. The BOOTSTRAP
string below is an argument of the WASI interpreter, not host Python code.
"""

import argparse
import errno
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import traceback

PYTHON_WASM_SHA256 = "5ce0cbeb843e6e5abf2d50c7158002e8333c26a40fbe27a7a52e66ee48cf64a8"
PYTHON_STDLIB_SHA256 = "74130c400ba5b818bf58bfc2f41fc075f4350cbb13e53099c84e9c0494ec5444"
MAX_RESULT_BYTES = 262144
MAX_REQUEST_BYTES = 2 * 1024 * 1024

BOOTSTRAP = r'''
import json, sys
if sys.version_info[:3] != (3, 12, 0) or sys.platform != "wasi":
    raise RuntimeError("incorrect WASI interpreter")
request = json.loads(sys.stdin.read())
namespace = {"__name__": "candidate"}
exec(compile(request["source"], "candidate.py", "exec"), namespace)
result = namespace["solve"](request["input"])
print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
'''


def digest(filename):
    checksum = hashlib.sha256()
    with filename.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bindings", required=True)
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--diagnostics", action="store_true")
    args = parser.parse_args()
    bindings = Path(args.bindings).resolve(strict=True)
    runtime = Path(args.runtime).resolve(strict=True)
    job = Path(args.job).resolve(strict=True)
    metadata = (bindings / "wasmtime-48.0.0.dist-info" / "METADATA").read_text()
    if "\nVersion: 48.0.0\n" not in metadata:
        raise RuntimeError("unverified Wasmtime bindings")
    sys.path.insert(0, str(bindings))
    import wasmtime

    wasm = runtime / "bin" / "python-3.12.0.wasm"
    stdlib = runtime / "usr" / "local" / "lib"
    if digest(wasm) != PYTHON_WASM_SHA256 or digest(stdlib / "python312.zip") != PYTHON_STDLIB_SHA256:
        raise RuntimeError("unverified WASI interpreter")
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise RuntimeError("request limit")
    request = json.loads(raw)
    if not isinstance(request["source"], str) or len(request["source"].encode("utf-8")) > 65536:
        raise RuntimeError("source limit")
    if not isinstance(request["inputs"], list) or not 1 <= len(request["inputs"]) <= 7:
        raise RuntimeError("suite limit")

    config = wasmtime.Config()
    config.consume_fuel = True
    config.epoch_interruption = True
    config.parallel_compilation = False
    # Default compilation cache is disabled. Only this immutable, hash-pinned
    # interpreter is compiled; no native modules are deserialized or imported.
    engine = wasmtime.Engine(config)
    module = wasmtime.Module.from_file(engine, str(wasm))
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    results = []
    for case_input in request["inputs"]:
        output, diagnostics = bytearray(), bytearray()
        state = {"bytes": 0, "overflow": False}

        def collect(target):
            def write(data):
                state["bytes"] += len(data)
                if state["bytes"] > MAX_RESULT_BYTES:
                    state["overflow"] = True
                    return -errno.EIO
                target.extend(data)
                return len(data)
            return write

        with tempfile.NamedTemporaryFile(dir=job, prefix="stdin-", mode="wb") as stdin:
            stdin.write(json.dumps({"source": request["source"], "input": case_input}, ensure_ascii=False).encode("utf-8"))
            stdin.flush()
            wasi = wasmtime.WasiConfig()
            wasi.argv = ["python", "-I", "-S", "-c", BOOTSTRAP]
            wasi.env = []
            wasi.stdin_file = stdin.name
            wasi.stdout_custom = collect(output)
            wasi.stderr_custom = collect(diagnostics)
            # This is the ONLY preopen, with read-only capabilities. In
            # particular there is no host root, jobdir, network, or host env.
            wasi.preopen_dir(str(stdlib), "/usr/local/lib", fs_mutable=False)
            store = wasmtime.Store(engine)
            store.set_limits(memory_size=64 * 1024 * 1024, table_elements=20000, instances=1, tables=1, memories=1)
            store.set_fuel(2_000_000_000)
            store.set_epoch_deadline(1)
            store.set_wasi(wasi)
            timer = threading.Timer(2.0, engine.increment_epoch)
            error = None
            try:
                instance = linker.instantiate(store, module)
                timer.start()
                instance.exports(store)["_start"](store)
            except wasmtime.ExitTrap as trap:
                if trap.code != 0:
                    error = "candidate_error"
            except wasmtime.Trap as trap:
                error = "timeout" if trap.trap_code in (wasmtime.TrapCode.INTERRUPT, wasmtime.TrapCode.OUT_OF_FUEL) else "candidate_error"
            finally:
                timer.cancel()
                if timer.ident is not None:
                    timer.join()
                store.close()
            if state["overflow"]:
                error = "output_limit"
            result_output = ""
            if error is None:
                try:
                    # Parse data, never candidate code. Invalid raw control
                    # characters must not expand sixfold in the outer host
                    # protocol and turn a candidate failure into a 503.
                    actual = json.loads(output.decode("utf-8"))
                    result_output = json.dumps(actual, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
                    if len(result_output) > MAX_RESULT_BYTES:
                        error = "output_limit"
                        result_output = ""
                except (ValueError, UnicodeError, RecursionError):
                    error = "invalid_output"
            # Failed cases never need their untrusted stdout in the protocol.
            # Successful output is bounded ASCII JSON: outer escaping can at
            # most double it, keeping all seven results below the host cap.
            results.append({"output": result_output, "error": error,
                            "diagnostic": diagnostics.decode("utf-8", errors="replace")[-1500:]})
    return {"results": results}


if __name__ == "__main__":
    try:
        response = main()
    except Exception:
        if "--diagnostics" in sys.argv:
            traceback.print_exc(file=sys.stderr)
        # Host failures are infrastructure problems, not a candidate's 0/N.
        # Do not leak local paths or any host data into candidate diagnostics.
        response = {"error": "runtime_unavailable"}
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
