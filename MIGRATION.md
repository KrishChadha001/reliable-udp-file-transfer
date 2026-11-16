# Migration record

This file records replacements before legacy code is removed.

| Old location/function | New location | Status |
|---|---|---|
| `rudp.protocol.Packet` | `rudp.protocol.Packet` | Kept; header/CRC remains shared |
| `rudp.protocol.ReceiveWindow` | `rudp.protocol.ReceiveWindow` | Kept; Selective Repeat semantics retained |
| `rudp.udp` transfer path | `rudp.sender.send_file` and `rudp.receiver.receive_file` | Removed; replacement loopback/fault coverage passes |
| `rudp.tcp` comparison transport | none; TCP is outside this project scope | Removed because the target is a UDP teaching project |
| `rudp.congestion.Rtt` | `rudp.congestion.Rtt` | Simplified to required estimator and bounds |
| `rudp.congestion.Aimd` | `rudp.congestion.Aimd` | Simplified to required slow start/AIMD rules |
| `rudp.common` control/security helpers | `rudp.sender`, `rudp.receiver`, `rudp.models` | Removed; old request/security features are out of scope |
| `run.sh` | README command examples | Removed because the project uses direct Python commands |
| `tools/netem.sh` | `rudp.channel.LossyChannel` | Removed; portable in-process fault injection replaces Linux-only NetEm |
| `tools/validate_ubuntu.sh` | unittest and Python standard-library checks | Removed because validation is no longer Ubuntu-specific |
| `rudp.cli` | `rudp.__main__` send/recv commands | Removed; replacement CLI coverage passes |
| `rudp.ui` | `app.py` | Removed; GUI replacement and transfer tests pass |
| `FaultProxy` in integration tests | `rudp.channel.LossyChannel` | New real-socket fault tests added; old fixture retained temporarily |

## Planned removals

The old request/pull protocol, TCP comparison path, terminal menu, and security
features specific to remote file requests will be removed only after replacement
tests pass. Each removal will be one commit and added to this table with the
reason.
