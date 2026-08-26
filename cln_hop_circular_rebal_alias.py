#!/usr/bin/env python3

import subprocess
from subprocess import PIPE
import sys
import json
import re
import random
import os
import argparse
from cln_lib import init_cln, call_rpc, verify_env

parser = argparse.ArgumentParser(description="Core Lightning Circular Rebalance by Alias or SCID",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument("alias_or_scid", help="Target inbound channel alias or SCID")
parser.add_argument("hop", type=int, help="Maximum number of hops (maxhops)")
parser.add_argument("ppm", type=int, nargs="?", default=100, help="Maximum ppm to pay (maxppm)")
parser.add_argument("maxoutppm", type=int, nargs="?", default=60, help="Maximum out ppm (maxoutppm)")
parser.add_argument("amount", type=int, nargs="?", default=400000, help="Total amount to rebalance in satoshis")
parser.add_argument("splitsize", type=int, nargs="?", default=100000, help="Split size in satoshis")
parser.add_argument("--cli", default=os.environ.get("CLN_CLI", "lightning-cli"), help="your lightning-cli command")

cmd_args, unknown_args = parser.parse_known_args()
config = vars(cmd_args)

clncli = [config["cli"]]
if "CLN_DIR" in os.environ:
    clncli.extend(["--lightning-dir", os.environ["CLN_DIR"]])
clncli.extend(unknown_args)
init_cln(clncli)

# Verify connectivity
verify_env(config["cli"])

def parse_msat(val):
    if isinstance(val, str):
        val = val.lower().strip()
        if val.endswith("msat"):
            val = val[:-4]
        elif val.endswith("sat"):
            val = val[:-3] + "000"
        return int(val)
    return int(val)

def get_scid(alias):
    alias_lower = alias.lower()
    peers_res = call_rpc("listpeers")
    if "error" in peers_res:
        raise Exception(f"Failed to list peers: {peers_res['error']}")
    peers = peers_res.get("peers", [])
    
    matching_peers = []
    for p in peers:
        p_id = p["id"]
        # Lookup node details in gossip to find alias
        n_res = call_rpc("listnodes", p_id)
        p_alias = ""
        if "nodes" in n_res and n_res["nodes"]:
            p_alias = n_res["nodes"][0].get("alias", "")
        
        if alias_lower in p_id.lower() or alias_lower in p_alias.lower():
            matching_peers.append((p_id, p_alias))
            
    if len(matching_peers) == 0:
        raise Exception("no alias or peer matches: " + alias)
    elif len(matching_peers) > 1:
        # Check if we have an exact match to disambiguate
        exact_matches = [m for m in matching_peers if m[1].lower() == alias_lower]
        if len(exact_matches) == 1:
            target_peer_id = exact_matches[0][0]
        else:
            names = ", ".join([f"{m[1]} ({m[0][:10]})" for m in matching_peers])
            raise Exception(f"more than 1 peer matches '{alias}': {names}")
    else:
        target_peer_id = matching_peers[0][0]
        
    # Get active normal channel SCIDs for this peer
    scids = []
    p_res = call_rpc("listpeerchannels", target_peer_id)
    if "channels" in p_res:
        for ch in p_res["channels"]:
            if ch.get("state") == "CHANNELD_NORMAL" and "short_channel_id" in ch:
                scids.append(ch["short_channel_id"])
                
    if not scids:
        raise Exception("no ready channels for " + alias)
    
    if len(scids) > 1:
        return random.choice(scids)
    return scids[0]

mypubkey_res = call_rpc("getinfo")
if "error" in mypubkey_res:
    raise Exception(f"Failed to get pubkey: {mypubkey_res['error']}")
mypubkey = mypubkey_res.get("id")

def get_channel_info(scid):
  channels_res = call_rpc("listchannels", scid)
  channels = channels_res.get("channels", [])
  r = {}
  if len(channels) == 2:
    for channel in channels:
      if channel["source"] == mypubkey:
        peer_chans_res = call_rpc("listpeerchannels", channel["destination"])
        peer_chans = peer_chans_res.get("channels", [])
        for chn in peer_chans:
          if "short_channel_id" in chn and chn["short_channel_id"] == scid:
            r["channel_size"] = parse_msat(chn["total_msat"])
            r["channel_balance"] = parse_msat(chn["to_us_msat"])
            r["ratio"] = r["channel_balance"]/r["channel_size"]

        r["local_fee_base"] = channel["base_fee_millisatoshi"]
        r["local_fee_ppm"] = channel["fee_per_millionth"]
      else:
        r["remote_fee_base"] = channel["base_fee_millisatoshi"]
        r["remote_fee_ppm"] = channel["fee_per_millionth"]

  return r

# Main Logic
to_scid = config["alias_or_scid"] if re.match(r'^\d+x\d+x\d+$', config["alias_or_scid"]) else get_scid(config["alias_or_scid"])
hop = config["hop"]

if hop > 10:
  raise Exception("hop > 10, makes no sense")

def record_rebalance(scid, amount, avg_cost):
  script_dir = os.path.dirname(os.path.abspath(__file__))
  records_path = os.environ.get("CLN_REBALANCE_RECORDS_FILE", os.path.join(script_dir, "rebalance_records"))

  records = {}
  if os.path.exists(records_path):
    try:
      with open(records_path, "r") as f:
        records = json.load(f)
    except Exception as e:
      print(f"Warning: Could not read {records_path}: {e}", file=sys.stderr)
      records = {}

  if scid not in records:
    records[scid] = []

  records[scid].append([amount, avg_cost])

  try:
    with open(records_path, "w") as f:
      json.dump(records, f, indent=2)
    print(f"\n[Saved to {records_path}] {scid}: ({amount} sat @ {avg_cost} ppm)")
  except Exception as e:
    print(f"Error writing to {records_path}: {e}", file=sys.stderr)

def do_rebal(s, ppm):
  print(json.dumps(get_channel_info(to_scid), indent = 2, separators=(',', ': ')))

  splitsize = config["splitsize"]
  size_rand = splitsize / 10
  splitamount = int(size_rand * 9 + random.randrange(int(size_rand)))
  tmp = int(s / splitamount)
  size_rebal = tmp * splitamount

  # Circular pull parameters
  rpc_args = [
      "circular-pull",
      "-k",
      "splits=2",
      f"splitamount={splitamount}",
      f"maxhops={hop}",
      "attempts=100",
      f"maxoutppm={config['maxoutppm']}",
      f"inscid={to_scid}",
      f"maxppm={ppm}",
      f"amount={size_rebal}"
  ]

  print("\nWe are going to call circular pull with these parameters:")
  print('call_rpc(\n    ' + ',\n    '.join([json.dumps(a) for a in rpc_args]) + '\n)')

  print("\nWould you like to proceed? [Y/n]: ", end='')
  sys.stdout.flush()
  proceed = sys.stdin.readline().rstrip()
  if proceed.lower() not in ["y", ""]:
    print("Aborted.")
    return

  # Circular pull run using call_rpc
  result = call_rpc(*rpc_args)

  print(json.dumps(result, indent = 2, separators=(',', ': ')))

  if isinstance(result, dict) and "successes" in result and isinstance(result["successes"], dict):
    total_weighted_ppm = 0
    total_amount = 0
    for peer, ppm_dict in result["successes"].items():
      if isinstance(ppm_dict, dict):
        for ppm_str, amt in ppm_dict.items():
          try:
            ppm_val = float(ppm_str)
            amt_val = float(amt)
            total_weighted_ppm += ppm_val * amt_val
            total_amount += amt_val
          except (ValueError, TypeError):
            continue

    if total_amount > 0:
      rebalanced_amount = result.get("rebalanced_amount", int(total_amount))
      avg_cost = round(total_weighted_ppm / total_amount, 2)
      if avg_cost.is_integer():
        avg_cost = int(avg_cost)
      record_rebalance(to_scid, rebalanced_amount, avg_cost)

  print("[continue: Y/n?]: ", end='')
  sys.stdout.flush()
  line = sys.stdin.readline().rstrip()
  if line.lower() == "y" or line == "":
    print(f"[ppm: {ppm}?]: ", end='')
    sys.stdout.flush()
    line = sys.stdin.readline().rstrip()
    ppm = ppm if line == "" else int(line)

    print(f"[size: {s}? /n *n]: ", end='')
    sys.stdout.flush()
    line = sys.stdin.readline().rstrip()
    if line != "":
      m = re.match(r'^([/*])(\d+(?:\.\d+)?)$', line)
      if m:
        if m.group(1) == '/':
          s = int(s / float(m.group(2)))
        else:
          s = int(s * float(m.group(2)))
      else:
        s = int(line)

    do_rebal(s, ppm)

do_rebal(config["amount"], config["ppm"])
