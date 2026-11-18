# ADR 0011 — Projects and attachments on the existing vault

**Status:** accepted (v0.1)

**Decision.** A project is one encrypted record (kind `project`) holding its structured state
(status, 9-block canvas, notes, milestones, file metadata); its body is regenerated for search.
Attachments are sealed with a key derived from the vault master key and stored as opaque `.pbx`
files whose names are random ids; originals' names live only in the encrypted record. Summaries
and next steps are deterministic so Nila is useful with no model.

**Consequences.** Sync and backup reuse the existing record machinery. Notes are capped (500) and
files at 8 MB. Project text is classified like any record and is not attached to online requests.
