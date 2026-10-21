# Internal Mac workflow time limits and diagnostics

`.github/workflows/build-mac-app.yml` is an internal unsigned candidate venue.
Its workflow definition ref and digest are separate from the explicitly reviewed
checkout SHA and tree. Record both identities before a confirmation run.

The job has a 95-minute limit. Checkout, uv, Node and Go setup each have five
minutes; private directory reservation has one, Xcode selection and Go validation
each have two. Build and inspect has 45 minutes, including original preparation,
packaging and the helper's existing bounded diagnostic packaging. Each artifact
upload has five minutes. These explicit limits total 85 minutes, leaving ten
minutes for action finalization within the job budget. This is an upper budget,
not an expected build duration or a guarantee that finalization completes.

After successful packaging, the build step requires the exact candidate ZIP and
inner receipt. It also requires the documented `HappyRanch-*.zip` upload pattern
to match exactly that one expected ZIP; an extra matching ZIP fails the step.
The success upload requires workflow success, a successful build-step outcome
and no cancellation. It uploads the admitted ZIP, inner receipt and existing
diagnostic JSON files. Missing candidate/receipt or an extra matching diagnostic
ZIP prevents success upload. The existing portable layout verifier retains its
failure-step condition and path format; these checks add no helper or test seam.

Failure makes the failure diagnostics upload eligible when the private run root
was reserved. A separate cancellation diagnostics step requires cancellation and
the preceding failure upload's recorded outcome to be `skipped`. Cancellation can
replace the current failure status, so status alone cannot establish whether an
upload was attempted. A preceding upload with outcome `success`, `failure` or
`cancelled`, or an unavailable outcome, excludes the cancellation upload. This
also excludes a second attempt when cancellation interrupts the first upload or
arrives after it finishes. The steps share the diagnostics-only
artifact name and select only the setup status, preflight and existing bounded
diagnostic files; neither publishes an app candidate. Their conditions avoid
writing the same artifact twice. A failed diagnostics upload remains a failure;
the cancellation step does not retry it.
Before reservation, no diagnostic root exists. Before the build, setup status may
be the only available file. Missing diagnostic files are reported by the upload
action; upload failure remains a failure and does not establish candidate success.

Step timeout can leave time for this upload if the runner remains available.
Explicit cancellation, job timeout, runner loss, or interrupted helper diagnostic
packaging can prevent or interrupt uploads. Cancellation diagnostics are best
effort. These workflow limits do not establish helper child-process cleanup;
named helper limits and owned-child cleanup belong to the package delivery owner.
Automatic action finalization remains subject to runner and job availability.

For an interrupted run, retain the actual GitHub run/attempt conclusion, raw logs
and any available artifacts. A cancellation request acknowledgement is not a
terminal conclusion. Read back the same run; do not dispatch another run to
resolve ambiguity. Record skipped/unavailable diagnostics explicitly. Source
review, lint and collected logs do not establish a valid app, notices, installation
or public release.

GitHub documents [workflow cancellation](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-cancellation),
[step outcomes](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts#steps-context),
[status expressions](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions#status-check-functions)
and [workflow timeout syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).
