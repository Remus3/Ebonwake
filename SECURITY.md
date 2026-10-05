# Security Policy

## What this project is, in one paragraph

Ebonwake is a companion for Black Desert Online. It runs locally: a Python
standard-library server bound to `127.0.0.1` and an Electron dashboard plus a
click-through overlay. It has no accounts, no telemetry and no hosted service.
It makes read-only calls to public web APIs and reads only the game's session
log and screenshots the player takes.

## Supported versions

There are no releases yet. The `main` branch is the supported version, and the
fix for anything reported goes there.

## Reporting a vulnerability

Use GitHub's private reporting: **Security -> Report a vulnerability** on this
repository, which opens a
[private security advisory](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
visible only to the maintainer.

Please do not open a public issue for anything that would expose someone's data
before it is fixed. Expect a first response within a week; this is a one-person
project, so that is a realistic figure rather than a service level.

## In scope

- **Anything that leaks a player identifier or a machine path** - a family or
  character name, an account id, an email, a local path - into a commit, a log
  that leaves the machine, or a network request it does not belong in.
- The local server answering anything other than loopback, or a page served by
  it being able to drive actions it should not.
- Code execution or file writes outside the documented paths
  (`ops/runtime/`, `config/local.json`).
- A dependency vulnerability that is actually reachable from this code.
- **Anything that would make Ebonwake touch the game process** - memory, input,
  injection, packets, client files. That is a safety bug here, not a feature.

## Out of scope

- **Vulnerabilities in Black Desert Online itself.** Report those to Pearl
  Abyss, not here.
- **Cheats, exploits, macros or anti-cheat bypasses.** Not accepted in an issue,
  a pull request or a security report. See
  [What it never does](README.md#what-it-never-does).
- Findings from automated scanners with no demonstrated impact on this code.

## If you are reporting a leak, do not include the leaked value

Describe where it comes out and how you triggered it. A path to reproduce is
enough; a real identifier in a public report is a second leak on top of the
first.
