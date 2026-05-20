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
parser.add_argument("--all", action=argparse.BooleanOptionalAction, help="all info, could be heavy operation")
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
info = call_rpc("getinfo")
mypubkey = info.get("id")

# Batch get node aliases
node_aliases = {}
nodes_res = call_rpc("listnodes")
if "nodes" in nodes_res:
    for n in nodes_res["nodes"]:
        node_aliases[n["nodeid"]] = n.get("alias", n["nodeid"][:20])

# Batch get all local channels
all_chans = []
total_cap = 0
outbound_cap = 0
peer_states = {}

peers_res = call_rpc("listpeers")
if "peers" in peers_res:
    for p in peers_res["peers"]:
        peer_states[p["id"]] = p["connected"]

# CLN's listpeerchannels without an ID returns all channels
channels_res = call_rpc("listpeerchannels")
if "channels" in channels_res:
    for ch in channels_res["channels"]:
        if "short_channel_id" in ch and ch["state"] == "CHANNELD_NORMAL":
            all_chans.append(ch)
            total_cap += ch["total_msat"]
            outbound_cap += ch["to_us_msat"]

# 2. Optional: Batch fetch remote fees from gossip
remote_fees = {}
if config["all"]:
    print("Fetching gossip channel data...", file=sys.stderr)
    # This can be heavy on nodes with 100k+ channels, but usually okay for 100-node nodes
    gossip_res = call_rpc("listchannels")
    if "channels" in gossip_res:
        for gc in gossip_res["channels"]:
            if gc["source"] != mypubkey:
                remote_fees[gc["short_channel_id"]] = gc["fee_per_millionth"]

# 3. Process and Sort
all_chans.sort(key=lambda c: c["to_us_msat"] / c["total_msat"])

# 4. Display Results
for ch in all_chans:
    peer_id = ch["peer_id"]
    alias = node_aliases.get(peer_id, "node not exist in gossip")
    ratio = ch["to_us_msat"] / ch["total_msat"]
    connected = peer_states.get(peer_id, False)
    scid = ch["short_channel_id"]
    
    remote_fee_ppm = remote_fees.get(scid, -1)

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
