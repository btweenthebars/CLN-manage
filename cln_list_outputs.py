import json
import os
import subprocess
from subprocess import PIPE
import sys
from termcolor import colored
import argparse

parser = argparse.ArgumentParser(description="Core Lightning Wallet Output List",
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
            return {"error": j.stderr.decode().strip()}
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

verify_env()

funds = call_rpc("listfunds")
if "error" in funds:
    print(colored(f"Error listing funds: {funds['error']}", "red"), file=sys.stderr)
    sys.exit(1)

outputs = funds.get("outputs", [])
# Sort by blockheight (newest first), unconfirmed (9999999) at top
outputs.sort(key=lambda o: o.get("blockheight", 9999999), reverse=True)

print(f"{'STATUS':<12} {'RES':<3} {'TXID:OUT':<70} {'HEIGHT':<8} {'AMOUNT (sats)':>15}")
print("-" * 112)

total_confirmed = 0
total_unconfirmed = 0

for o in outputs:
    status = o["status"]
    reserved = "R" if o.get("reserved", False) else " "
    txid_out = f"{o['txid']}:{o['output']}"
    height = o.get("blockheight", "pending")
    amount_sat = int(o["amount_msat"] / 1000)
    
    if status == "confirmed":
        status_colored = colored(status, "green")
        total_confirmed += amount_sat
    elif status == "unconfirmed":
        status_colored = colored(status, "yellow")
        total_unconfirmed += amount_sat
    elif status == "spent":
        status_colored = colored(status, "red")
    else:
        status_colored = status

    print(f"{status_colored:<21} {reserved:<3} {txid_out:<70} {height:<8} {amount_sat:>15,}")

print("-" * 112)
print(f"Total Confirmed:   {total_confirmed:>15,} sats")
print(f"Total Unconfirmed: {total_unconfirmed:>15,} sats")
print(f"Total Balance:     {total_confirmed + total_unconfirmed:>15,} sats")
