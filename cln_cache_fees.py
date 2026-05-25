import json
import os
import subprocess
from subprocess import PIPE
import sys
from termcolor import colored
import argparse

parser = argparse.ArgumentParser(description="Core Lightning Peer Fee Cache Generator",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--cli", default=os.environ.get("CLN_CLI", "lightning-cli"), help="your lightning-cli command")
cmd_args, unknown_args = parser.parse_known_args()
config = vars(cmd_args)

clncli = [config["cli"]]
if "CLN_DIR" in os.environ:
    clncli.extend(["--lightning-dir", os.environ["CLN_DIR"]])
clncli.extend(unknown_args)

def call_rpc(*args):
    args = clncli + list(args)
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

def verify_env():
    info = call_rpc("getinfo")
    if "id" not in info:
        print(colored("Error: Could not connect to Core Lightning.", "red"), file=sys.stderr)
        if "error" in info:
            print(f"  RPC Error: {info['error']}", file=sys.stderr)
        sys.exit(1)
    return info

def main():
    verify_env()
    print(colored("Connecting to node and retrieving peers list...", "cyan"))
    
    peers_res = call_rpc("listpeers")
    if "error" in peers_res:
        print(colored(f"Error calling listpeers: {peers_res['error']}", "red"), file=sys.stderr)
        sys.exit(1)
        
    peers = peers_res.get("peers", [])
    if not peers:
        print(colored("No peers found.", "yellow"))
        peer_fees = {}
    else:
        peer_fees = {}
        total = len(peers)
        print(colored(f"Found {total} peers. Gathering remote fee distributions...", "cyan"))
        for idx, p in enumerate(peers):
            p_id = p["id"]
            alias = p.get("alias", p_id[:20])
            print(f"[{idx+1}/{total}] Fetching channels for {colored(alias, 'yellow')} ({p_id})...")
            
            chan_res = call_rpc("listchannels", "-k", f"source={p_id}")
            if "error" in chan_res:
                print(colored(f"  Error fetching channels for peer {p_id}: {chan_res['error']}", "red"), file=sys.stderr)
                continue
                
            chans = chan_res.get("channels", [])
            peer_fees[p_id] = sorted([c["fee_per_millionth"] for c in chans])
            
    # Write to peer_fees_cache.json in the script's directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    cache_path = os.path.join(script_dir, "peer_fees_cache.json")
    
    try:
        with open(cache_path, "w") as f:
            json.dump(peer_fees, f, indent=2)
        print(colored(f"\nSuccessfully wrote cache to: {cache_path}", "green"))
    except Exception as e:
        print(colored(f"\nError writing cache file: {str(e)}", "red"), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
