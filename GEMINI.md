# CLN-manage Engineering Standards

## Coding Standards
- **Argument Parsing**: Always use `argparse.parse_known_args()` to allow for script-specific flags while passing unknown arguments directly to `lightning-cli`.
- **Environment Support**: Respect `CLN_CLI` and `CLN_DIR` environment variables for node connection.
- **Performance**:
    - Use **backward paging** for `listforwards` queries (starting from the maximum index and stopping once the target timestamp is reached).
    - Use **indexed filters** (e.g., `in_channel=...`, `out_channel=...`) when reviewing specific channels to avoid global scans.
    - **Batch RPC calls** like `listpeers` and `listnodes` at script startup to avoid redundant per-channel lookups in loops.
    - **Cache** expensive or repetitive RPC data (like peer fee distributions).
- **UX**: Use `termcolor` for status and ratio coloring (Red < 0.2, Yellow > 0.8 for liquidity ratios).

## Project Structure
- All utility scripts should be self-contained and located in the root directory.
- `README.md` must be updated with every new script.
