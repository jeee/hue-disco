Hybrid official bridge rebuild (v14)

Fixed:
- Keep official_bridge pairing path that works on real bridges.
- In /api mode, prefer v2 entertainment discovery mapped back to numeric /groups/<id> when available.
- Stream activation now falls back from v1 /groups/<id>/action to v2 entertainment start on real bridges.
- UI template tolerates missing state.lights.

Still worth verifying:
- entertainment_group_id settles back to numeric 200-style ids on the target bridge
- start action succeeds without manual config edits
