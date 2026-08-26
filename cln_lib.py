import json
import os
import subprocess
from subprocess import PIPE
import sys
import time
from termcolor import colored

_clncli = ["lightning-cli"]
_peer_fees_cache = None
_peer_fees_cache_decay = 0

def init_cln(clncli_list):
    """Initialize the CLN CLI command parameters."""
    global _clncli
    _clncli = clncli_list

def call_rpc(*args):
    """Execute a command against lightning-cli and return the parsed JSON response.
    Robustly parses errors from stdout/stderr when returncode is non-zero.
    """
    args = _clncli + list(args)
    try:
        j = subprocess.run(args, stdout=PIPE, stderr=PIPE)
        if j.returncode != 0:
            err_msg = j.stderr.decode().strip()
            if not err_msg and j.stdout:
                try:
                    err_json = json.loads(j.stdout)
                    err_msg = err_json.get("message", err_json.get("error", j.stdout.decode().strip()))
                except:
                    err_msg = j.stdout.decode().strip()
            return {"error": err_msg or f"Exit code {j.returncode}"}
        return json.loads(j.stdout)
    except Exception as e:
        return {"error": str(e)}

def verify_env(cli_path="lightning-cli"):
    """Verify that we can successfully connect to the Core Lightning node."""
    info = call_rpc("getinfo")
    if "id" not in info:
        print(colored("Error: Could not connect to Core Lightning.", "red"), file=sys.stderr)
        print(f"Check if your CLN_CLI and CLN_DIR environment variables are set correctly.", file=sys.stderr)
        print(f"  CLN_CLI: {cli_path}", file=sys.stderr)
        print(f"  CLN_DIR: {os.environ.get('CLN_DIR', 'Default (~/.lightning)')}", file=sys.stderr)
        if "error" in info:
            print(f"  RPC Error: {info['error']}", file=sys.stderr)
        sys.exit(1)
    return info

def _load_cache():
    global _peer_fees_cache, _peer_fees_cache_decay
    now = time.time()
    if _peer_fees_cache is not None and (now - _peer_fees_cache_decay <= 3 * 3600):
        return
    
    _peer_fees_cache = {}
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cache_path = os.environ.get("CLN_PEER_FEES_CACHE_FILE", os.path.join(script_dir, "peer_fees_cache.json"))
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                _peer_fees_cache = json.load(f)
        except Exception as e:
            print(colored(f"Warning: Failed to load peer fees cache from {cache_path}: {e}", "yellow"), file=sys.stderr)
    _peer_fees_cache_decay = now

def get_peer_fees(peer_id, call_rpc_func=call_rpc):
    """Get sorted list of remote peer fee PPMs.
    Loads from peer_fees_cache.json if available, or queries live via CLN RPC.
    """
    _load_cache()
    
    if peer_id not in _peer_fees_cache:
        peer_chans = call_rpc_func("listchannels", "-k", f"destination={peer_id}").get("channels", [])
        _peer_fees_cache[peer_id] = sorted([c["fee_per_millionth"] for c in peer_chans])
        
    return _peer_fees_cache[peer_id]

def get_rebalance_records_file():
    """Get target path for saving rebalance records (defaults to current working directory)."""
    if "CLN_REBALANCE_RECORDS_FILE" in os.environ:
        return os.environ["CLN_REBALANCE_RECORDS_FILE"]
    cwd = os.getcwd()
    cwd_file = os.path.join(cwd, "rebalance_records")
    if os.path.exists(cwd_file):
        return cwd_file
    cwd_json = os.path.join(cwd, "rebalance_records.json")
    if os.path.exists(cwd_json):
        return cwd_json
    return os.path.join(cwd, "rebalance_records")

def load_rebalance_records():
    """Load and merge rebalance records, prioritizing the directory where the command runs."""
    cwd = os.getcwd()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.dirname(script_dir)

    candidate_dirs = [
        cwd,  # Priority 1: directory where command was run
        script_dir,
        os.path.join(parent_dir, "CLN-illtry"),
        os.path.join(parent_dir, "CLN-manage"),
        os.path.join(parent_dir, "suez"),
        os.path.join(script_dir, "..", "CLN-illtry"),
        os.path.join(script_dir, "..", "suez"),
    ]
    if "CLN_DIR" in os.environ:
        candidate_dirs.append(os.environ["CLN_DIR"])
    if "CLN_REBALANCE_RECORDS_FILE" in os.environ:
        custom_file = os.environ["CLN_REBALANCE_RECORDS_FILE"]
        candidate_dirs.insert(0, os.path.dirname(custom_file) or cwd)

    file_names = ["rebalance_records", "rebalance_records.json"]
    if "CLN_REBALANCE_RECORDS_FILE" in os.environ:
        custom_base = os.path.basename(os.environ["CLN_REBALANCE_RECORDS_FILE"])
        if custom_base and custom_base not in file_names:
            file_names.insert(0, custom_base)

    seen_paths = set()
    merged_records = {}

    for d in candidate_dirs:
        for fname in file_names:
            p = os.path.abspath(os.path.join(d, fname))
            if p not in seen_paths and os.path.isfile(p):
                seen_paths.add(p)
                try:
                    with open(p, "r") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        for scid, entries in data.items():
                            if isinstance(entries, list):
                                if scid not in merged_records:
                                    merged_records[scid] = []
                                for item in entries:
                                    if item not in merged_records[scid]:
                                        merged_records[scid].append(item)
                except Exception:
                    pass

    return merged_records
