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
    j = subprocess.run(args, stdout=PIPE, stderr=subprocess.DEVNULL)
    try:
        return json.loads(j.stdout)
    except:
        return {}

# 1. Gathering basic info (Batch aliases - High Speed)
print("Gathering node/channel info...", file=sys.stderr)
info = call_rpc("getinfo")
mypubkey = info.get("id")

node_aliases = {}
nodes_res = call_rpc("listnodes")
for n in nodes_res.get("nodes", []):
    node_aliases[n["nodeid"]] = n.get("alias", n["nodeid"][:20])

# 2. Reliable channel discovery loop (Original stable logic)
all_peers = call_rpc("listpeers").get("peers", [])
all_chans = []
total_cap = 0
outbound_cap = 0

for peer in all_peers:
    p_res = call_rpc("listpeerchannels", peer["id"])
    for ch in p_res.get("channels", []):
        if "short_channel_id" in ch and ch["state"] == "CHANNELD_NORMAL":
            all_chans.append([ch, peer])
            total_cap += ch["total_msat"]
            outbound_cap += ch["to_us_msat"]

all_chans.sort(key=lambda c: c[0]["to_us_msat"] / c[0]["total_msat"])

# 3. Display Results
for ch, peer in all_chans:
    peer_id = peer["id"]
    alias = node_aliases.get(peer_id, "node not exist in gossip")
    ratio = ch["to_us_msat"] / ch["total_msat"]
    scid = ch["short_channel_id"]
    
    # 5th column: Remote fee ppm
    remote_fee_ppm = -1
    # Try modern 'updates' field first (fast)
    if "updates" in ch and "remote" in ch["updates"]:
        remote_fee_ppm = ch["updates"]["remote"].get("fee_proportional_millionths", -1)
    
    # Fallback to original listchannels if missing and --all is set
    if remote_fee_ppm == -1 and config["all"]:
        chan_res = call_rpc("listchannels", scid)
        for gc in chan_res.get("channels", []):
            if gc.get("source") == peer_id:
                remote_fee_ppm = gc["fee_per_millionth"]

    print("%s\t%s\t%.2f\t%d\t%d\t%s\t%s" % (
        scid,
        '{:20s}'.format(alias[:20]),
        ratio,
        ch.get("fee_proportional_millionths", 0),
        remote_fee_ppm,
        "connected" if peer.get("connected") else "disconnected",
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
