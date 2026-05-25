import json
import os
import subprocess
from subprocess import PIPE
import sys
from termcolor import colored
import argparse
import re
from cln_lib import init_cln, call_rpc, verify_env

parser = argparse.ArgumentParser(description="Core Lightning Alias/ID Lookup",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("query", help="Alias substring or Node ID to search for")
parser.add_argument("--cli", default=os.environ.get("CLN_CLI", "lightning-cli"), help="your lightning-cli command")
parser.add_argument("--all", action="store_true", help="Search all nodes in gossip, not just peers")
cmd_args, unknown_args = parser.parse_known_args()
config = vars(cmd_args)

clncli = [config["cli"]]
if "CLN_DIR" in os.environ:
    clncli.extend(["--lightning-dir", os.environ["CLN_DIR"]])
clncli.extend(unknown_args)
init_cln(clncli)

verify_env()

query = config["query"].lower()
results = []

if config["all"]:
    print(colored(f"Searching all nodes for '{query}'...", "cyan"), file=sys.stderr)
    nodes_res = call_rpc("listnodes")
    nodes = nodes_res.get("nodes", [])
else:
    print(colored(f"Searching peers for '{query}'...", "cyan"), file=sys.stderr)
    peers_res = call_rpc("listpeers")
    peers = peers_res.get("peers", [])
    nodes = []
    for p in peers:
        p_id = p["id"]
        n_res = call_rpc("listnodes", p_id)
        if "nodes" in n_res and n_res["nodes"]:
            nodes.append(n_res["nodes"][0])
        else:
            # Fallback if node not in gossip
            nodes.append({"nodeid": p_id, "alias": p_id[:20]})

for n in nodes:
    nodeid = n["nodeid"]
    alias = n.get("alias", "")
    
    if query in nodeid.lower() or query in alias.lower():
        # Get channel info for this node if it's a peer
        scids = []
        p_res = call_rpc("listpeerchannels", nodeid)
        if "channels" in p_res:
            for ch in p_res["channels"]:
                if "short_channel_id" in ch:
                    scid = ch["short_channel_id"]
                    if ch["state"] == "CHANNELD_NORMAL":
                        scids.append(colored(scid, "green"))
                    else:
                        scids.append(colored(scid, "yellow"))
        
        results.append({
            "nodeid": nodeid,
            "alias": alias,
            "scids": " ".join(scids)
        })

if not results:
    print(colored("No matches found.", "red"))
else:
    for r in results:
        print(f"{colored(r['nodeid'], 'cyan')}\t{r['alias']:<20}\t{r['scids']}")
