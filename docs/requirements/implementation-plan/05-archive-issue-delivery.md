# Iteration 5: Final Archive and Issue Delivery

## Goal

Archive successful briefing deliverables in this repository and complete the
GitHub Issue as the final delivery page.

## External Acceptance Feature

A successful request ends with a `completed` Issue containing links to the final
PPT, manifest, QA summary, and archive location.

## Scope

- Create the final archive directory under `briefings/YYYY/MM/issue-<number>-<slug>/`.
- Copy only curated final artifacts into the archive.
- Include `source.md`, `brief.pptx`, `manifest.json`, and `qa-summary.md`.
- Compute artifact metadata for `manifest.json`.
- Include at minimum `artifact_id`, `kind`, `logical_path`, `storage_backend`,
  `sha256`, `size_bytes`, and `download_url` for archived artifacts.
- Use Git LFS for PPTX files if large PPT artifacts are committed.
- Update the Issue to `completed`.
- Add a final Issue comment with archive path, PPT link, manifest link, QA
  summary link, and commit or GitHub file links.

## Out of Scope

- Object storage migration.
- Multi-artifact download portals.
- Long-term retention automation.
- Replacing GitHub Issues with a custom delivery UI.
- Archiving failed or QA-failed candidate outputs as final deliverables.

## Key Decisions

- This repository is both the service repository and final archive repository.
- The archive contains final curated artifacts only.
- `manifest.json` is the stable metadata contract so storage can later migrate
  from Git LFS to object storage without changing API semantics.
- The Issue is complete only when it links to the archived artifacts.
- Failed jobs should end with clear Issue status, not partial archive delivery.

## Implementation Notes

- The archive slug should be deterministic and derived from the Issue number and
  request title.
- `source.md` should be the source snapshot used for generation, not a later
  edited version.
- `brief.pptx` should be the QA-approved deck.
- `qa-summary.md` should be the final QA summary associated with that deck.
- `manifest.json` should record hash and size after the final files are in
  place.
- The final Issue comment should be concise but complete enough for a requester
  to retrieve the deliverables without reading local logs.

## E2E Acceptance Test

### Preconditions

- Iterations 1 through 4 are complete.
- A request can generate a QA-approved PPT.
- The worker can write to the repository archive directory.
- The repository storage policy for PPTX files is configured.

### Steps

1. From outside the home network, submit a complete briefing request.
2. Open the returned Issue URL.
3. Track progress only through the Issue.
4. Wait for the Issue to reach `completed`.
5. Open the archive path linked from the Issue.
6. Confirm the archive directory exists under
   `briefings/YYYY/MM/issue-<number>-<slug>/`.
7. Confirm the archive contains `source.md`, `brief.pptx`, `manifest.json`, and
   `qa-summary.md`.
8. Compute the SHA-256 hash and file size of `brief.pptx`.
9. Compare them with the `manifest.json` values.
10. Open or download the PPT link from the Issue.
11. Open the manifest and QA summary links from the Issue.

### Expected Result

- The Issue reaches `completed`.
- The Issue contains usable final delivery links.
- The archive contains exactly the expected final artifact set for the job.
- `manifest.json` metadata matches the archived PPT file.
- The final delivery can be understood and accessed from the Issue alone.

## Risks & Diagnostics

- **Archive path collision:** include the Issue number in the directory name.
- **PPTX too large for normal Git storage:** use Git LFS and keep metadata in the
  manifest.
- **Hash mismatch:** compute metadata only after files are placed in the final
  archive directory.
- **Broken GitHub links:** verify final links from the Issue after commit or file
  publication.
- **Scratch files accidentally archived:** archive only the explicit curated file
  list.
- **Future storage migration:** preserve manifest fields so `download_url` can be
  changed later without changing the API response shape.

## Done Criteria

- Successful jobs create a final archive directory under `briefings/`.
- The archive contains `source.md`, `brief.pptx`, `manifest.json`, and
  `qa-summary.md`.
- Artifact metadata in `manifest.json` is correct.
- The Issue is updated to `completed` and includes final delivery links.

