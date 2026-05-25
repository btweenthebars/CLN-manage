import json
import os
import subprocess
from subprocess import PIPE
import sys
from termcolor import colored

_clncli = ["lightning-cli"]
_peer_fees_cache = None

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
    global _peer_fees_cache
    if _peer_fees_cache is not None:
        return
    
    _peer_fees_cache = {}
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cache_path = os.path.join(script_dir, "peer_fees_cache.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r") as f:
                _peer_fees_cache = json.load(f)
        except Exception as e:
            print(colored(f"Warning: Failed to load peer fees cache: {e}", "yellow"), file=sys.stderr)

def get_peer_fees(peer_id, call_rpc_func=call_rpc):
    """Get sorted list of remote peer fee PPMs.
    Loads from peer_fees_cache.json if available, or queries live via CLN RPC.
    """
    _load_cache()
    
    if peer_id not in _peer_fees_cache:
        peer_chans = call_rpc_func("listchannels", "-k", f"source={peer_id}").get("channels", [])
        _peer_fees_cache[peer_id] = sorted([c["fee_per_millionth"] for c in peer_chans])
        
    return _peer_fees_cache[peer_id]
