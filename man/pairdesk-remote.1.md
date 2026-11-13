---
title: PAIRDESK-REMOTE
section: 1
header: User Commands
footer: PairDesk 2.9.3
date: November 2026
---

# NAME

pairdesk-remote - PairDesk's side of a connection on the remote computer

# SYNOPSIS

**pairdesk-remote** **serve**|**pair-gate**|**sockcat** ...

# DESCRIPTION

Not meant to be run by hand. PairDesk's link service runs it as the forced command of each
paired computer's key, so that a key can only run the requests listed in it: status, USB device
sharing, shared folders, the desktop's certificate fingerprint, signing out the local session,
unpairing, and exchanging PairDesk updates (a received package is only kept; installing it needs
an administrator of that computer). A computer that isn't paired yet knocks with a fresh random key; **pairdesk-knock** lets such a
key in to **pair-gate** only, which can only ask for a one-time pairing code to be shown, or pair
with that code (a code works only for the key that asked for it).

# SEE ALSO

**pairdesk**(1)
