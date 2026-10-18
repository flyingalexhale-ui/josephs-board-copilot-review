# How to resume the deep code review (for Claude)
State at 2026-10-18 10:45Z: all 8 reviews done (A-H folders here, each findings.md + tests). Verification paused by Alex at 2026-10-18 00:08Z after A0 and A11 (3 checkers each) and A2 reproduce (1 of 3). Resume only when Alex says go.
- Code under review: GitHub flyingalexhale-ui/josephs-board branch review-2026-10-16 (commit d7d568f), check out to /home/claude/josephs-board. Never touch main; never push settings.env.
- Brief: REVIEW-BRIEF.md. Groups to verify: verify/items/<gid>.md + verify/index.txt (132 groups, made by verify/parse.py + groups.py from the findings files). Workflow args: verify/args.json.
- Workflow script: verify/workflow/verify-board-review-wf_1cca168c-929.js, with its journal (journal.jsonl). Finished checker results: verify/results-so-far.json; their test scripts: verify/work/.
- Same container: Workflow({scriptPath: <session>/workflows/scripts/verify-board-review-wf_1cca168c-929.js, resumeFromRunId: "wf_1cca168c-929"}) reuses the finished checks.
- New container: copy this folder's A-H, verify/ and REVIEW-BRIEF.md into the new scratchpad as rev/<A-H>, verify/, REVIEW-BRIEF.md; change the constant S in the script to the new scratchpad; skip finished checks (A0, A11 all lenses; A2 repro) by feeding results-so-far.json in, then run the rest one checker at a time (Alex's choice; "no rush at all").
- After verification: completeness rounds (anything missed, loop until dry), then ONE ranked plain-language list for Alex. Report only; Alex picks fixes; fixes ship as one jb-*.zip.
