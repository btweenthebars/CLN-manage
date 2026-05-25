import json
import os
import subprocess
import sys
import asyncio
from google.antigravity import Agent, LocalAgentConfig
from google.antigravity.hooks import policy
from google.antigravity.utils.interactive import run_interactive_loop


# Path to the virtual environment's python interpreter to run the utility scripts
VENV_PYTHON = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python3")
if not os.path.exists(VENV_PYTHON):
    # Fallback to standard python3 if .venv python is not found
    VENV_PYTHON = sys.executable

def run_script(script_name: str, args: list[str]) -> str:
    """Helper to run a python script in the workspace using the virtual env python."""
    script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), script_name)
    try:
        # Pass CLN environment variables if present
        env = os.environ.copy()
        res = subprocess.run([VENV_PYTHON, script_path] + args, capture_output=True, text=True, env=env)
        output = res.stdout
        if res.stderr:
            output += "\n--- Logs/Stderr ---\n" + res.stderr
        return output
    except Exception as e:
        return f"Error executing {script_name}: {str(e)}"

# Define custom tools for the Agent

def list_channels(sort: str = "time", show_all: bool = False) -> str:
    """List Core Lightning (CLN) channels with liquidity ratios, colored status, and Short Channel IDs (SCIDs).

    Args:
        sort: Sort channels by 'time' (creation time) or 'ratio' (liquidity ratio). Defaults to 'time'.
        show_all: If True, fetch and display remote channel policy/fee information. Defaults to False.
    """
    args = []
    if sort in ["time", "ratio"]:
        args.extend(["--sort", sort])
    if show_all:
        args.append("--all")
    return run_script("cln_list_channels.py", args)

def last_forward(daysago: float = 1.0, sort: str = "time") -> str:
    """List recent settled forwards on the Core Lightning (CLN) node with fee and volume statistics.

    Args:
        daysago: Look back N days for forwards (can be a float, e.g. 0.5 for 12 hours). Defaults to 1.0.
        sort: Sort forwards by 'fee', 'ppm', 'volume', or 'time'. Defaults to 'time'.
    """
    args = ["--daysago", str(daysago), "--sort", sort]
    return run_script("cln_last_forward.py", args)

def channel_review(aliases: list[str] = None, xdays: list[int] = None, peer_id: str = None, absent_forward: int = -1, ratio_min: float = 0.0, ratio_max: float = 1.0) -> str:
    """Perform an in-depth review of channel performance for specific peers or all channels.

    Args:
        aliases: A list of peer node aliases or ID substrings to filter by. Defaults to None.
        xdays: A list of day ranges to gather stats for (e.g. [1, 7, 30]). Defaults to [1, 7, 30].
        peer_id: The specific peer pubkey to review. Defaults to None.
        absent_forward: Review peers that have had NO forwards in the last N days. Defaults to -1.
        ratio_min: Filter peers where we have liquidity ratio >= ratio_min. Defaults to 0.0.
        ratio_max: Filter peers where we have liquidity ratio <= ratio_max. Defaults to 1.0.
    """
    args = ["--non-interactive"]
    if xdays:
        args.extend(["--xdays"] + [str(x) for x in xdays])
    if peer_id:
        args.extend(["--peer-id", peer_id])
    if absent_forward != -1:
        args.extend(["--absent-forward", str(absent_forward)])
    if ratio_min != 0.0:
        args.extend(["--ratio-min", str(ratio_min)])
    if ratio_max != 1.0:
        args.extend(["--ratio-max", str(ratio_max)])
    if aliases:
        args.extend(aliases)
    return run_script("cln_channel_review.py", args)

def list_outputs() -> str:
    """Display wallet UTXOs, confirmed and unconfirmed balances, and reservation status of outputs on the Core Lightning (CLN) node."""
    return run_script("cln_list_outputs.py", [])

def alias_to_id(query: str, search_all: bool = False) -> str:
    """Look up Core Lightning (CLN) node IDs and Short Channel IDs (SCIDs) by alias or substring.

    Args:
        query: Alias substring or Node ID to search for.
        search_all: If True, search all nodes in gossip, not just current peer nodes. Defaults to False.
    """
    args = [query]
    if search_all:
        args.append("--all")
    return run_script("cln_alias_to_id.py", args)

def cache_fees() -> str:
    """Batch query and cache remote peer fee PPM distributions to a local JSON file to speed up channel reviews."""
    return run_script("cln_cache_fees.py", [])

# Agent system instructions
SYSTEM_INSTRUCTIONS = """You are a Core Lightning (CLN) management assistant.
You help operators monitor and optimize their LN nodes.
You have tools to query channels, forwards, wallet outputs, lookup aliases, and cache peer fees.
When asked to analyze or report status:
1. Choose the most specific tool for the task.
2. Present tables and key details clearly to the user.
3. Highlight issues like low liquidity channels or nodes with no forward activity.
"""

async def main():
    config = LocalAgentConfig(
        tools=[list_channels, last_forward, channel_review, list_outputs, alias_to_id, cache_fees],
        system_instructions=SYSTEM_INSTRUCTIONS,
        # Allow custom tools to run without safety prompt (standard command prompt still applies to raw CLI)
        policies=[policy.allow_all()]
    )
    
    print("Initializing Core Lightning management agent...")
    async with Agent(config) as agent:
        await run_interactive_loop(agent)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting agent loop.")
