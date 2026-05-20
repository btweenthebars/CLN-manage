import json
import os
import subprocess
from subprocess import PIPE
import time
from math import ceil
import sys
from termcolor import colored
import argparse

parser = argparse.ArgumentParser(description="c-lightning channel review",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("--cli", default=os.environ.get("CLN_CLI", "lightning-cli"), help="your lightning-cli command")
parser.add_argument("--cli-args", default=[], nargs='+', help="lightning-cli arguments ommitting --")
parser.add_argument("--all", action=argparse.BooleanOptionalAction, help="all info, currently used for remote policy fallback")
parser.add_argument("--sort", default="time", choices=["time", "ratio"], help="sort channels by creation time or liquidity ratio")
cmd_args = parser.parse_args()
config = vars(cmd_args)

ONE_M = 1000000000

clncli = [config["cli"]]
if "CLN_DIR" in os.environ:
    clncli.extend(["--lightning-dir", os.environ["CLN_DIR"]])
clncli.extend(list(map((lambda a: "--" + a), config["cli_args"])))

def call_rpc(*args):
    args = clncli + list(args)
    try:
        j = subprocess.run(args, stdout=PIPE, stderr=PIPE)
        if j.returncode != 0:
            return {"error": j.stderr.decode().strip()}
        return json.loads(j.stdout)
    except Exception as e:
        return {"error": str(e)}

def verify_env():
    info = call_rpc("getinfo")
    if "id" not in info:
        print(colored("Error: Could not connect to Core Lightning.", "red"), file=sys.stderr)
        print(f"Check if your CLN_CLI and CLN_DIR environment variables are set correctly.", file=sys.stderr)
        print(f"  CLN_CLI: {config['cli']}", file=sys.stderr)
        print(f"  CLN_DIR: {os.environ.get('CLN_DIR', 'Default (~/.lightning)')}", file=sys.stderr)
        if "error" in info:
            print(f"  RPC Error: {info['error']}", file=sys.stderr)
        sys.exit(1)
    return info

def format_fee(base_msat, ppm):
    if base_msat is None or ppm is None:
        return "?.???/?"
    base_sat = base_msat / 1000.0
    if base_sat == int(base_sat):
        base_str = str(int(base_sat))
    else:
        base_str = "%.3f" % base_sat
    return f"{base_str}/{ppm}"

def scid_to_int_tuple(scid):
    try:
        return tuple(map(int, scid.split('x')))
    except:
        return (0, 0, 0)

# 1. Verify environment and gathering node info
verify_env()
print("Gathering node/channel info...", file=sys.stderr)

node_aliases = {}
nodes_res = call_rpc("listnodes")
if "nodes" in nodes_res:
    for n in nodes_res["nodes"]:
        node_aliases[n["nodeid"]] = n.get("alias", n["nodeid"][:20])

all_chans = []
total_cap = 0
outbound_cap = 0

# 2. Channel discovery loop
all_peers = call_rpc("listpeers").get("peers", [])
for peer in all_peers:
    p_id = peer["id"]
    p_res = call_rpc("listpeerchannels", p_id)
    for ch in p_res.get("channels", []):
        if "short_channel_id" in ch and ch["state"] == "CHANNELD_NORMAL":
            ch["peer_id"] = p_id
            ch["peer_connected"] = peer.get("connected", False)
            all_chans.append(ch)
            total_cap += ch["total_msat"]
            outbound_cap += ch["to_us_msat"]

# 3. Sorting
if config["sort"] == "ratio":
    # Sort by liquidity ratio
    all_chans.sort(key=lambda c: c["to_us_msat"] / c["total_msat"])
else:
    # Default: Sort by creation time (using SCID as proxy)
    all_chans.sort(key=lambda c: scid_to_int_tuple(c["short_channel_id"]))

# 4. Display Results
for ch in all_chans:
    peer_id = ch["peer_id"]
    alias = node_aliases.get(peer_id, peer_id[:20])
    ratio = ch["to_us_msat"] / ch["total_msat"]
    scid = ch["short_channel_id"]
    
    # Color SCID based on connection status
    connected = ch.get("peer_connected", False)
    scid_str = colored(scid, "green" if connected else "red")
    
    # liquidity/size in Million sats
    liq_m = ch["to_us_msat"] / ONE_M
    cap_m = ch["total_msat"] / ONE_M
    cap_str = "%.2f/%.2f" % (liq_m, cap_m)
    
    # Local policy
    local_base = ch.get("fee_base_msat")
    local_ppm = ch.get("fee_proportional_millionths")
    
    # Remote policy
    remote_base = None
    remote_ppm = None
    
    if "updates" in ch and "remote" in ch["updates"]:
        remote_base = ch["updates"]["remote"].get("fee_base_msat")
        remote_ppm = ch["updates"]["remote"].get("fee_proportional_millionths")
    
    if remote_ppm is None and config["all"]:
        chan_res = call_rpc("listchannels", scid)
        for gc in chan_res.get("channels", []):
            if gc.get("source") == peer_id:
                remote_base = gc.get("base_fee_msat")
                remote_ppm = gc.get("fee_per_millionth")

    print("%s\t%s\t%.2f\t%s\t%s\t%s\t%s" % (
        scid_str,
        '{:20s}'.format(alias[:20]),
        ratio,
        '{:12s}'.format(cap_str),
        '{:10s}'.format(format_fee(local_base, local_ppm)),
        '{:10s}'.format(format_fee(remote_base, remote_ppm)),
        peer_id
    ))

if total_cap > 0:
    print("")
    print("Total Capacity: %.2fM, Outbound: %.2fM, Ratio: %.2f" % (
        total_cap / ONE_M,
        outbound_cap / ONE_M,
        outbound_cap / total_cap
    ))
else:
    print("\nNo active channels found.")
