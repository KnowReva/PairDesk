# Security

## Reporting a problem

Please report security problems privately, not as a public issue: use GitHub's "Report a vulnerability"
(Security tab of this repository). You'll get an answer within a week. Please include the PairDesk version
(`pairdesk doctor` shows it) and the steps to reproduce.

## How PairDesk keeps the other computer safe

- **One encrypted connection on PairDesk's own port** (default 4722), open only while remote access is on.
  It runs as you, never as root. No passwords are accepted on it, only keys.
- **A paired computer can only run PairDesk's own requests** (desktop, folders, USB, camera, status),
  never a shell.
- **Pairing works like a one-time password, with nothing shared between installs:**
  - The computer you sit at knocks with a new random key, made for this pairing attempt only and deleted
    afterwards.
  - A key the other computer doesn't know is only let in to the pairing gate (`/usr/lib/pairdesk/pairdesk-knock`).
    There it can do two things: ask for a one-time code to be shown on that computer's screen (at most 5 times a
    minute), and pair with that code.
  - The code has 16 characters, works once, for 10 minutes, with 3 tries, and only for the key that asked for it.
  - Both computers prove they know the code without sending it, tied to the other computer's identity (its host
    key), so a computer in between can't take part.
  - After pairing, each computer has its own key for the other, unique to that pairing.
  - Settings > "Accept pairing requests only after I press Pair a new computer" refuses all knocks unless a code
    is already showing.
- **Codes are hidden on screen until you press Show.**
- **Installing anything as administrator** (add-ons, updates sent by a paired computer) always asks for an
  administrator's password on that computer. A paired computer can never install software by itself.
- **USB devices sent from another computer** are made usable only by PairDesk's users, and only those devices
  (a udev rule on devices under `vhci_hcd`).

PairDesk 2.8 and earlier used one well-known pairing key shared by every install (it, too, could only ask for a
code). 2.9 no longer has it: an older computer can still pair with a 2.9 one, but a 2.9 computer asks you to
update an older one first.
