# Third-party notices

Nila is proprietary software. This file lists every place where an **idea,
algorithm, file format or wording** was taken from another project, as required
by that project's licence. No third-party source file is vendored into this
repository; each item below was re-written for Nila and then tested.

---

## OpenJarvis — Apache License 2.0

- Upstream: <https://github.com/open-jarvis/OpenJarvis>
- Copyright: Open Jarvis Contributors / Stanford Hazy Research &
  Scaling Intelligence Lab.
- Licence text: <https://www.apache.org/licenses/LICENSE-2.0>
- What we took: the **idea and the normalisation table**, not the code.

| Nila file | OpenJarvis source | What was used |
|---|---|---|
| `brain/util/http.py` → `_disguised_ipv4()` | `src/openjarvis/security/ssrf.py` → `_disguised_ipv4()` | Normalise decimal / hex / octal / short-dotted IPv4 before deciding a URL is public. Re-written against `socket.inet_aton`; Nila's own private-range and redirect rules unchanged. |
| `tests/test_audit_redirect_credentials.py` (disguised-loopback block) | `tests/security/test_ssrf_disguised.py` | The list of hostile encodings to test. |

Nothing else from OpenJarvis is used yet. Features Nila plans to adopt (loop
guard, injection patterns, data-boundary scan, document index, scheduler) are
recorded in `NILA_OPENJARVIS_ANALYSIS_TA.md` and will be added to this table as
they land.

---

*Last updated: 2026-11-04*
