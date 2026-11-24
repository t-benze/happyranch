# Sequential prompt and bind ordering

The private schema-shell C27 case maps real queue/Dispatcher/run_step and
SequentialDispatcher.dispatch to prompt construction, Render to
`_generic_node_instruction`, and Commit to `TaskInvocation.commit` and
`_session_bound_uncommitted`. Render accepts the complete queued/null or
executing/bound tuple. It does not persist the prospective invocation SID.
Commit independently revalidates current identity and cancellation before
binding. Cancellation or assignment/selection drift between rendering and
commit prevents launch. Restart stutters over retained admitted state; fresh
Save/Allow is not a prerequisite for that state.

The model abstracts authority, active selection and principal into `identity`;
Python validation, budgets, native closure and actual providers remain separate
obligations. Weak fairness assumes an available queue/host with no permanent
interference; it is not an unconditional daemon liveness claim.

Run the pinned jar and Java recipe from
[task lifecycle](../task-lifecycle/README.md#run-tlc), with module
`SequentialLaunch`, `current.cfg` and `pre-fix.cfg` and separate task-scratch
metadirs. Current must satisfy the three invariants and Progress; pre-fix must
violate Progress by stuttering in queued/null before rendering. Results belong
in the PR/task evidence. No new CI gate or operating authority follows.
