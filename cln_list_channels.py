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
parser.add_argument("--all", action=argparse.BooleanOptionalAction, help="all info, currently used for remote policy fallback")
parser.add_argument("--sort", default="time", choices=["time", "ratio"], help="sort channels by creation time or liquidity ratio")
cmd_args, unknown_args = parser.parse_known_args()
config = vars(cmd_args)

ONE_M = 1000000000

clncli = [config["cli"]]
if "CLN_DIR" in os.environ:
    clncli.extend(["--lightning-dir", os.environ["CLN_DIR"]])
clncli.extend(unknown_args)

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
    if not scid or 'x' not in scid:
        return (9999999, 9999999, 9999999) # Put channels without SCID at the end of time sort
    try:
        return tuple(map(int, scid.split('x')))
    except:
        return (9999999, 9999999, 9999999)

def print_channel(ch, alias):
    peer_id = ch["peer_id"]
    ratio = ch["to_us_msat"] / ch["total_msat"] if ch["total_msat"] > 0 else 0
    scid = ch.get("short_channel_id")
    state = ch["state"]
    
    # Identifier column (SCID or State)
    connected = ch.get("peer_connected", False)
    if scid:
        id_str = colored(scid, "green" if connected else "red")
    else:
        id_str = colored(state, "yellow")
    
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
    
    if remote_ppm is None and config["all"] and scid:
        chan_res = call_rpc("listchannels", scid)
        for gc in chan_res.get("channels", []):
            if gc.get("source") == peer_id:
                remote_base = gc.get("base_fee_msat")
                remote_ppm = gc.get("fee_per_millionth")

    print("%s\t%s\t%.2f\t%s\t%s\t%s\t%s" % (
        '{:20s}'.format(id_str), # Padded because id_str might contain ANSI codes
        '{:20s}'.format(alias[:20]),
        ratio,
        '{:12s}'.format(cap_str),
        '{:10s}'.format(format_fee(local_base, local_ppm)),
        '{:10s}'.format(format_fee(remote_base, remote_ppm)),
        peer_id
    ))

# 1. Verify environment and gathering node info
verify_env()
print("Gathering node/channel info...", file=sys.stderr)

node_aliases = {}
nodes_res = call_rpc("listnodes")
if "nodes" in nodes_res:
    for n in nodes_res["nodes"]:
        node_aliases[n["nodeid"]] = n.get("alias", n["nodeid"][:20])

normal_chans = []
other_chans = []
total_cap = 0
outbound_cap = 0

# 2. Channel discovery loop
all_peers = call_rpc("listpeers").get("peers", [])
for peer in all_peers:
    p_id = peer["id"]
    p_res = call_rpc("listpeerchannels", p_id)
    for ch in p_res.get("channels", []):
        ch["peer_id"] = p_id
        ch["peer_connected"] = peer.get("connected", False)
        if ch["state"] == "CHANNELD_NORMAL":
            normal_chans.append(ch)
            total_cap += ch["total_msat"]
            outbound_cap += ch["to_us_msat"]
        else:
            other_chans.append(ch)

# 3. Sorting
if config["sort"] == "ratio":
    normal_chans.sort(key=lambda c: c["to_us_msat"] / c["total_msat"] if c["total_msat"] > 0 else 0)
else:
    normal_chans.sort(key=lambda c: scid_to_int_tuple(c.get("short_channel_id")))

# Sort other channels by state then SCID
other_chans.sort(key=lambda c: (c["state"], scid_to_int_tuple(c.get("short_channel_id"))))

# 4. Display Results
for ch in other_chans:
    alias = node_aliases.get(ch["peer_id"], "node not exist in gossip")
    print_channel(ch, alias)

if other_chans and normal_chans:
    print("-" * 100)

for ch in normal_chans:
    alias = node_aliases.get(ch["peer_id"], "node not exist in gossip")
    print_channel(ch, alias)

if total_cap > 0:
    print("")
    print("Total Capacity: %.2fM, Outbound: %.2fM, Ratio: %.2f" % (
        total_cap / ONE_M,
        outbound_cap / ONE_M,
        outbound_cap / total_cap
    ))
elif not other_chans:
    print("\nNo channels found.")
