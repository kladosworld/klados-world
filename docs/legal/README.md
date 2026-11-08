# Legal publication resources

The exact Product Owner-supplied Terms and Privacy Policy are included. The owner
explicitly approved final publication of this exact SHA256 pair in Codex chat on
2026-11-08; `APPROVED.json` records that approval. This is not merge or deployment
authorization. Do not substitute older drafts or modify the approved bytes.

- TERMS.md: `fd35bf05367f96fd09eefea026e2ec7d80cbe6642616b30dbd4b0572bd93f2d2`
- PRIVACY.md: `0a8f2d464b00522d41dd39fbcf10f8b46b2f8cf6c7fac1f276fb9045638243e5`

The exact operator identification "Klados World, EU" is present in the supplied
Privacy Policy. It is not present in the supplied Terms; no clause was inserted.
The renderer supports the supplied backslash/two-space Markdown hard breaks
without modifying source or API document text.

For any future revision, the Product Owner must supply the exact `TERMS.md` and `PRIVACY.md` files as UTF-8
(without BOM), LF newlines and a final newline, and approve their SHA256 hashes and
wording, including "Klados World, EU" exactly where supplied. Do not normalize,
paraphrase or strip approved text. If its formatting is unsupported, request a
reviewed renderer extension or owner-approved typographic version; never silently edit it.

## Publication contract

- Canonical sources: `docs/legal/TERMS.md`, `docs/legal/PRIVACY.md`.
- Approval record: `docs/legal/APPROVED.json`, schema version 1, `status: approved`,
  a nonempty `approval_reference` to the owner's approval, and a `sha256` map with
  exactly the two filenames and their lowercase 64-digit hashes. This record is
  review evidence, not a cryptographic owner signature; only an approved PR may
  change it. Never set it to approved merely because a file exists.
- The build hook includes only these three filenames as `arena/legal/*` in the wheel.
  Docker context permits exactly these legal resources; review notes stay out.
  The installed loader prefers that package directory and never falls back to a
  source directory when packaged resources are present.
- Default final mode is off: both legal APIs return the existing preparation message;
  metadata and sitemap do not describe unpublished files as published policies.
- `KLADOS_LEGAL_FINAL=1` requires both complete files and a valid approval record at
  startup, and revalidates before serving documents. A missing/changed/pending pair
  fails closed. No partial publication and no silent editorial filtering.
- Served JSON `text` re-encoded as UTF-8 must equal each approved source file byte for
  byte. HTML is formatted Markdown with existing page chrome, not byte-identical
  source markup. Routes, headings, safe DOM rendering and official contacts remain.

The conservative renderer check rejects empty text, noncanonical encoding/newlines,
editorial/draft markers, unresolved placeholders, italics, deep headings, HTML,
images, code and unsupported links. Hash equality cannot determine legal completeness:
the owner must approve the complete pair. No legal sufficiency claim is made here.

## Release handoff for Claude (no operations performed)

1. Integrate in dependency order: #15, #16, then this focused legal-packaging PR.
   Preserve the reviewed #16 head until an authorized rebase/retarget. Do not merge
   constituent application PRs a second time.
2. Add only the owner-approved source files and manifest in a separately reviewable
   content commit. Verify their approved hashes before committing; run the existing
   legal, wheel and runtime-image tests. Never deploy the synthetic fixture image.
3. Freeze the resulting application commit/tree and the actual built runtime image
   identity/digest. The packaging-only head with a pending manifest is **not** a final
   legal-publication candidate; new content changes the commit/tree/image.
4. Claude separately forwards `KLADOS_LEGAL_FINAL=1` to web startup in reviewed
   operations configuration, updates the application pin, and rehearses the exact
   image offline/sandbox with the existing V2 image-policy and preservation checks.
   This application PR does not edit compose, nginx, scripts or runbooks.
5. On that image, verify both `website.legal_file(page)` paths exist under installed
   `arena/legal`; each file hash and each legal API text hash must match owner approval.
   Inspect `/privacy`, `/terms`, footer links and sitemap on desktop/mobile. No tick,
   resume, provider change, paid inference or production DB is needed.
6. A missing/invalid final pair must stop startup. Prefer fix-forward or a validated
   V2-compatible rollback. Any rollback that withdraws final policies needs explicit
   owner acceptance and Claude's release/containment review; never restore pre-V2
   public mock disclosure. No schema/data rollback is introduced.

## Evidence boundaries

Unit/HTTP tests use clearly synthetic approval records. A wheel test checks exact
resources and excludes a private-review canary. The existing Linux CI runtime build
also builds a separate **synthetic, non-published fixture image** with both documents
and proves installed-path/hash/API equality with networking disabled. The real
pending image must remain unpublished and refuse final startup. These tests prove
packaging mechanics, not receipt or approval of the owner's missing legal texts.

Owner file/hash and final-publication approval have been received for the pair above.
Merge/deployment approval, final-image selection and Claude's release rehearsal
remain separate gates. Complete native CI/runtime evidence is recorded in PR #17.
