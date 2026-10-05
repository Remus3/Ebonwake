<!--
CONTRIBUTING.md has the four rules; this template asks you to show your
working. Delete a section only if it genuinely does not apply, and say why.
-->

## What this changes

<!-- One or two sentences. Closes #N / roadmap plan NNN if it applies. -->

## The test you watched FAIL

<!-- Paste the red output, or describe the failure you saw before the fix. -->

## Suite result

<!-- Last line of `python -m pytest -q` and of `npm test --prefix app`. -->

```
```

## Checklist

- [ ] Both suites pass, from this run.
- [ ] **Nothing here touches the game** - no memory access, no injection, no
      input to the game window, no packets, no client files.
- [ ] No absolute path, account id, email, family/character name or secret in
      the diff or this description.
- [ ] 7-bit ASCII and LF everywhere, including this description.
- [ ] Docs updated if behaviour changed.
