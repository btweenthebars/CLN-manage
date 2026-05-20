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
parser.add_argument("--all", action=argparse.BooleanOptionalAction, help="all info, currently unused but kept for compatibility")
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
            return {"error": j.stderr.decode()}
        return json.loads(j.stdout)
    except Exception as e:
        return {"error": str(e)}

# 1. Gathering basic info (Batch calls)
print("Gathering node/channel info...", file=sys.stderr)

# Batch get node aliases
node_aliases = {}
nodes_res = call_rpc("listnodes")
if "nodes" in nodes_res:
    for n in nodes_res["nodes"]:
        node_aliases[n["nodeid"]] = n.get("alias", n["nodeid"][:20])

all_chans = []
total_cap = 0
outbound_cap = 0
peer_states = {}

# Try to get channels globally (v23.08+)
channels_res = call_rpc("listpeerchannels")
if "channels" in channels_res and channels_res["channels"]:
    for ch in channels_res["channels"]:
        if "short_channel_id" in ch and ch["state"] == "CHANNELD_NORMAL":
            all_chans.append(ch)
            total_cap += ch["total_msat"]
            outbound_cap += ch["to_us_msat"]
            # Save connectivity state from listpeerchannels
            peer_states[ch["peer_id"]] = ch.get("peer_connected", False)
else:
    # Fallback for older CLN versions
    print("Old CLN detected, falling back to per-peer channel lookup...", file=sys.stderr)
    peers_res = call_rpc("listpeers")
    if "peers" in peers_res:
        for p in peers_res["peers"]:
            peer_states[p["id"]] = p["connected"]
            # Some older versions might have channels inside listpeers
            p_chans = p.get("channels", [])
            if not p_chans:
                # If not in listpeers, we must call listpeerchannels per peer
                chan_lookup = call_rpc("listpeerchannels", p["id"])
                p_chans = chan_lookup.get("channels", [])
            
            for ch in p_chans:
                if "short_channel_id" in ch and ch["state"] == "CHANNELD_NORMAL":
                    # Ensure peer_id is present for consistency
                    ch["peer_id"] = p["id"]
                    all_chans.append(ch)
                    total_cap += ch["total_msat"]
                    outbound_cap += ch["to_us_msat"]

# 2. Process and Sort
all_chans.sort(key=lambda c: c["to_us_msat"] / c["total_msat"])

# 3. Display Results
for ch in all_chans:
    peer_id = ch["peer_id"]
    alias = node_aliases.get(peer_id, "node not exist in gossip")
    ratio = ch["to_us_msat"] / ch["total_msat"]
    connected = peer_states.get(peer_id, False)
    scid = ch["short_channel_id"]
    
    # Modern CLN includes remote policy in listpeerchannels
    remote_fee_ppm = -1
    if "updates" in ch and "remote" in ch["updates"]:
        remote_fee_ppm = ch["updates"]["remote"].get("fee_proportional_millionths", -1)
    
    # Fallback for older CLN
    if remote_fee_ppm == -1 and config["all"]:
        chan_res = call_rpc("listchannels", scid)
        if "channels" in chan_res:
            for gc in chan_res["channels"]:
                if gc["source"] == peer_id:
                    remote_fee_ppm = gc["fee_per_millionth"]

    print("%s\t%s\t%.2f\t%d\t%d\t%s\t%s" % (
        scid,
        '{:20s}'.format(alias[:20]),
        ratio,
        ch.get("fee_proportional_millionths", 0),
        remote_fee_ppm,
        "connected" if connected else "disconnected",
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
