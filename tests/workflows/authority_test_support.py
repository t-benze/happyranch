from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text


def ensure_coherent_authority(org) -> int:
    """Make a daemon route fixture a coherent real OrgState authority seam."""
    paths = OrgPaths(root=org.root)
    for reviewer in ("code_reviewer", "senior_dev"):
        if org.teams.team_for_agent(reviewer) is None:
            org.teams.add_worker("engineering", reviewer)

    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    for team in org.teams.teams():
        registration = org.teams.manager_for_team(team)
        identities = (*(((registration.name, "manager"),) if registration.name is not None else ()), *(
            (worker, "worker") for worker in registration.workers
        ))
        for name, role in identities:
            current = prompt_loader.load_agent(paths, name)
            if current is None:
                current = AgentDef(
                    name=name,
                    team=team,
                    role=role,
                    executor="claude",
                    allow_rules=(),
                    repos={},
                    enrolled_by="test-fixture",
                    enrolled_at_task="TASK-U2A-TEST",
                    enrolled_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                    system_prompt=f"prompt:{name}",
                    description=f"description:{name}",
                )
            elif current.team != team or current.role != role:
                current = replace(current, team=team, role=role)
            (paths.agents_dir / f"{name}.md").write_text(render_agent_text(current))

    org.workflow_authority.recover_or_publish(publisher="test-fixture")
    return org.workflow_authority.verify_admission_ready().generation


# Fixed schema1 historical admission DATA, never a callback or old-reader receipt.
# Authored independently using the retained @1 wire contract and desired values;
# constants are not obtained from the current authority/activation writers.
C5_SCHEMA1_HISTORY = {'authorization_bytes': b'{"activation_id":"workflow-activation:7dbd124ce2ff79975d018ce40e27bc93fc'
                        b'8814c0fda239fc356da8e21f90a044","activation_revision":1,"actor":{"princi'
                        b'pal_id":"founder","principal_kind":"human","proof_kind":"founder_bearer"'
                        b'},"authority":{"generation":1,"namespace":"org/alpha","snapshot_digest":'
                        b'"1748e893628dc107fa884d2cf44f5a69fcd5f7d69e5754042bf19adb0ee9ad44"},"cre'
                        b'ated_at":"2026-10-01T00:00:00+00:00","format":"workflow-authorization@1"'
                        b',"instance_id":"workflow-instance:360939a90b15321fac99bc4f5b513bb33da5d0'
                        b'c1e0bfb31dab5635ec286d2858","namespace":"org/alpha/workflow-instance/wor'
                        b'kflow-instance:360939a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635ec2'
                        b'86d2858","original_request":{"allowed_actions":["draft-document","submit'
                        b'-immutable-document","collect-review","approve-planning-input","return-t'
                        b'o-author"],"authority":{"generation":1,"namespace":"org/alpha","snapshot'
                        b'_digest":"1748e893628dc107fa884d2cf44f5a69fcd5f7d69e5754042bf19adb0ee9ad'
                        b'44"},"bindings":{"founder":{"kind":"human","principal":"founder","team":'
                        b'null},"implementer":{"kind":"agent","principal":"dev_agent","team":"engi'
                        b'neering"},"product-lead":{"kind":"agent","principal":"product_lead","tea'
                        b'm":"product"},"tester":{"kind":"agent","principal":"qa_engineer","team":'
                        b'"engineering"}},"eligible_replacements":{"founder":[],"implementer":[],"'
                        b'product-lead":[],"tester":[]},"expected_activation_revision":0,"inputs":'
                        b'[],"instance_id":"c5-retained-schema1","operation_key":"c5-retained-sche'
                        b'ma1","scope":{"brief":"Retained bounded schema1 document."},"template":{'
                        b'"definition_digest":"0d815497899d1e6f43022ec8c88574a36bbf295a05fbf00f411'
                        b'c8a8e324c7f57","identity_id":"workflow-template:07272c7fc560148bd4a83aef'
                        b'23fd8100c6fc8ac4bf64beef0af7794b2a7cc9a2","version":1}},"request_digest"'
                        b':"3f5e27e1c845b676e5defbfc64b7cd7fbd2f3cb57527271fe70f761db483238f","roo'
                        b't_task_id":"TASK-900","template":{"compiler_pin":"workflow-compiler@1","'
                        b'definition_digest":"0d815497899d1e6f43022ec8c88574a36bbf295a05fbf00f411c'
                        b'8a8e324c7f57","identity_id":"workflow-template:07272c7fc560148bd4a83aef2'
                        b'3fd8100c6fc8ac4bf64beef0af7794b2a7cc9a2","source_pin":"operator-input@1"'
                        b',"validator_pin":"workflow-validator@1","version":1,"version_id":"workfl'
                        b'ow-template-version:73a8d146c3aeef70da5129392093488d903f8ee053f287763417'
                        b'deb51b8c88cd"}}',
 'authorization_digest': 'aebae223e69de012675dd175eb316c5623f2b2fe8562ab7c2d8edaa488eace44',
 'authorization_id': 'workflow-authorization:d5dafb5d983a31917a2c1a1553b956a49491a2052149b9ae63d0c676cd7d704c',
 'binding_bytes': b'{"activation_id":"workflow-activation:7dbd124ce2ff79975d018ce40e27bc93fc8814'
                  b'c0fda239fc356da8e21f90a044","activation_revision":1,"authority":{"generation'
                  b'":1,"namespace":"org/alpha","snapshot_digest":"1748e893628dc107fa884d2cf44f5'
                  b'a69fcd5f7d69e5754042bf19adb0ee9ad44"},"authorization_revision_id":"workflow-'
                  b'authorization:d5dafb5d983a31917a2c1a1553b956a49491a2052149b9ae63d0c676cd7d70'
                  b'4c","bindings":{"founder":{"kind":"human","principal":"founder","team":null}'
                  b',"implementer":{"kind":"agent","principal":"dev_agent","team":"engineering"}'
                  b',"product-lead":{"kind":"agent","principal":"product_lead","team":"product"}'
                  b',"tester":{"kind":"agent","principal":"qa_engineer","team":"engineering"}},"'
                  b'eligible_replacements":{"founder":[],"implementer":[],"product-lead":[],"tes'
                  b'ter":[]},"format":"workflow-binding@1","instance_id":"workflow-instance:3609'
                  b'39a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635ec286d2858","scope":{"brie'
                  b'f":"Retained bounded schema1 document."},"template_version_id":"workflow-tem'
                  b'plate-version:73a8d146c3aeef70da5129392093488d903f8ee053f287763417deb51b8c88'
                  b'cd"}',
 'binding_digest': '5d40ab6cfe97ab47260333cd764bc6219a58f754889a84e09abda8cb80acea1a',
 'binding_id': 'workflow-binding:c365db4c52d8530246b4ed14d2e017f633b66b4b7479c84693402a5723b1d91c',
 'brief': 'Workflow initial drafting task. Produce a bounded document artifact only. Do not '
          'delegate, fan out, implement code, merge, or approve the document. Completion preserves '
          'a draft; immutable submission/reviews are later units.\n'
          '\n'
          'Retained bounded schema1 document.',
 'context_bytes': b'{"assignment_generation":1,"attempt_sequence":1,"authority_snapshot":{"activ'
                  b'e_policy_selectors":[{"selector":null,"team":"engineering"},{"selector":null'
                  b',"team":"product"}],"agents":[{"allow_rules":[],"description":"Retained sche'
                  b'ma1 fixture","executor":"claude","model":null,"name":"code_reviewer","repos"'
                  b':{},"role":"worker","status":"active","system_prompt_digest":"3608eb7a99d51e'
                  b'5e5cb67d90ae3ea066d6bec944052341fe34c0be3dcef315d8","team":"engineering"},{"'
                  b'allow_rules":[],"description":"Retained schema1 fixture","executor":"claude"'
                  b',"model":null,"name":"dev_agent","repos":{},"role":"worker","status":"active'
                  b'","system_prompt_digest":"3608eb7a99d51e5e5cb67d90ae3ea066d6bec944052341fe34'
                  b'c0be3dcef315d8","team":"engineering"},{"allow_rules":[],"description":"Retai'
                  b'ned schema1 fixture","executor":"claude","model":null,"name":"engineering_ma'
                  b'nager","repos":{},"role":"manager","status":"active","system_prompt_digest":'
                  b'"3608eb7a99d51e5e5cb67d90ae3ea066d6bec944052341fe34c0be3dcef315d8","team":"e'
                  b'ngineering"},{"allow_rules":[],"description":"Retained schema1 fixture","exe'
                  b'cutor":"claude","model":null,"name":"product_lead","repos":{},"role":"manage'
                  b'r","status":"active","system_prompt_digest":"3608eb7a99d51e5e5cb67d90ae3ea06'
                  b'6d6bec944052341fe34c0be3dcef315d8","team":"product"},{"allow_rules":[],"desc'
                  b'ription":"Retained schema1 fixture","executor":"claude","model":null,"name":'
                  b'"qa_engineer","repos":{},"role":"worker","status":"active","system_prompt_di'
                  b'gest":"3608eb7a99d51e5e5cb67d90ae3ea066d6bec944052341fe34c0be3dcef315d8","te'
                  b'am":"engineering"}],"machine_global_profiles":[],"org_slug":"alpha","reviewe'
                  b'r_agents":["code_reviewer"],"schema_version":1,"teams":[{"manager":"engineer'
                  b'ing_manager","name":"engineering","workers":["code_reviewer","dev_agent","qa'
                  b'_engineer"]},{"manager":"product_lead","name":"product","workers":[]}]},"aut'
                  b'horization":{"activation_id":"workflow-activation:7dbd124ce2ff79975d018ce40e'
                  b'27bc93fc8814c0fda239fc356da8e21f90a044","activation_revision":1,"actor":{"pr'
                  b'incipal_id":"founder","principal_kind":"human","proof_kind":"founder_bearer"'
                  b'},"authority":{"generation":1,"namespace":"org/alpha","snapshot_digest":"174'
                  b'8e893628dc107fa884d2cf44f5a69fcd5f7d69e5754042bf19adb0ee9ad44"},"created_at"'
                  b':"2026-10-01T00:00:00+00:00","format":"workflow-authorization@1","instance_i'
                  b'd":"workflow-instance:360939a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635'
                  b'ec286d2858","namespace":"org/alpha/workflow-instance/workflow-instance:36093'
                  b'9a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635ec286d2858","original_reque'
                  b'st":{"allowed_actions":["draft-document","submit-immutable-document","collec'
                  b't-review","approve-planning-input","return-to-author"],"authority":{"generat'
                  b'ion":1,"namespace":"org/alpha","snapshot_digest":"1748e893628dc107fa884d2cf4'
                  b'4f5a69fcd5f7d69e5754042bf19adb0ee9ad44"},"bindings":{"founder":{"kind":"huma'
                  b'n","principal":"founder","team":null},"implementer":{"kind":"agent","princip'
                  b'al":"dev_agent","team":"engineering"},"product-lead":{"kind":"agent","princi'
                  b'pal":"product_lead","team":"product"},"tester":{"kind":"agent","principal":"'
                  b'qa_engineer","team":"engineering"}},"eligible_replacements":{"founder":[],"i'
                  b'mplementer":[],"product-lead":[],"tester":[]},"expected_activation_revision"'
                  b':0,"inputs":[],"instance_id":"c5-retained-schema1","operation_key":"c5-retai'
                  b'ned-schema1","scope":{"brief":"Retained bounded schema1 document."},"templat'
                  b'e":{"definition_digest":"0d815497899d1e6f43022ec8c88574a36bbf295a05fbf00f411'
                  b'c8a8e324c7f57","identity_id":"workflow-template:07272c7fc560148bd4a83aef23fd'
                  b'8100c6fc8ac4bf64beef0af7794b2a7cc9a2","version":1}},"request_digest":"3f5e27'
                  b'e1c845b676e5defbfc64b7cd7fbd2f3cb57527271fe70f761db483238f","root_task_id":"'
                  b'TASK-900","template":{"compiler_pin":"workflow-compiler@1","definition_diges'
                  b't":"0d815497899d1e6f43022ec8c88574a36bbf295a05fbf00f411c8a8e324c7f57","ident'
                  b'ity_id":"workflow-template:07272c7fc560148bd4a83aef23fd8100c6fc8ac4bf64beef0'
                  b'af7794b2a7cc9a2","source_pin":"operator-input@1","validator_pin":"workflow-v'
                  b'alidator@1","version":1,"version_id":"workflow-template-version:73a8d146c3ae'
                  b'ef70da5129392093488d903f8ee053f287763417deb51b8c88cd"}},"authorization_revis'
                  b'ion_id":"workflow-authorization:d5dafb5d983a31917a2c1a1553b956a49491a2052149'
                  b'b9ae63d0c676cd7d704c","binding":{"activation_id":"workflow-activation:7dbd12'
                  b'4ce2ff79975d018ce40e27bc93fc8814c0fda239fc356da8e21f90a044","activation_revi'
                  b'sion":1,"authority":{"generation":1,"namespace":"org/alpha","snapshot_digest'
                  b'":"1748e893628dc107fa884d2cf44f5a69fcd5f7d69e5754042bf19adb0ee9ad44"},"autho'
                  b'rization_revision_id":"workflow-authorization:d5dafb5d983a31917a2c1a1553b956'
                  b'a49491a2052149b9ae63d0c676cd7d704c","bindings":{"founder":{"kind":"human","p'
                  b'rincipal":"founder","team":null},"implementer":{"kind":"agent","principal":"'
                  b'dev_agent","team":"engineering"},"product-lead":{"kind":"agent","principal":'
                  b'"product_lead","team":"product"},"tester":{"kind":"agent","principal":"qa_en'
                  b'gineer","team":"engineering"}},"eligible_replacements":{"founder":[],"implem'
                  b'enter":[],"product-lead":[],"tester":[]},"format":"workflow-binding@1","inst'
                  b'ance_id":"workflow-instance:360939a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31d'
                  b'ab5635ec286d2858","scope":{"brief":"Retained bounded schema1 document."},"te'
                  b'mplate_version_id":"workflow-template-version:73a8d146c3aeef70da512939209348'
                  b'8d903f8ee053f287763417deb51b8c88cd"},"binding_snapshot_id":"workflow-binding'
                  b':c365db4c52d8530246b4ed14d2e017f633b66b4b7479c84693402a5723b1d91c","format":'
                  b'"workflow-initial-draft-context@1","inputs":[],"intent_id":"f019d3c21791aee6'
                  b'9dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0","org_slug":"alpha","reques'
                  b't":{"allowed_actions":["draft-document","submit-immutable-document","collect'
                  b'-review","approve-planning-input","return-to-author"],"authority":{"generati'
                  b'on":1,"namespace":"org/alpha","snapshot_digest":"1748e893628dc107fa884d2cf44'
                  b'f5a69fcd5f7d69e5754042bf19adb0ee9ad44"},"bindings":{"founder":{"kind":"human'
                  b'","principal":"founder","team":null},"implementer":{"kind":"agent","principa'
                  b'l":"dev_agent","team":"engineering"},"product-lead":{"kind":"agent","princip'
                  b'al":"product_lead","team":"product"},"tester":{"kind":"agent","principal":"q'
                  b'a_engineer","team":"engineering"}},"eligible_replacements":{"founder":[],"im'
                  b'plementer":[],"product-lead":[],"tester":[]},"expected_activation_revision":'
                  b'0,"inputs":[],"instance_id":"c5-retained-schema1","operation_key":"c5-retain'
                  b'ed-schema1","scope":{"brief":"Retained bounded schema1 document."},"template'
                  b'":{"definition_digest":"0d815497899d1e6f43022ec8c88574a36bbf295a05fbf00f411c'
                  b'8a8e324c7f57","identity_id":"workflow-template:07272c7fc560148bd4a83aef23fd8'
                  b'100c6fc8ac4bf64beef0af7794b2a7cc9a2","version":1}},"template":{"approval":{"'
                  b'mode":"all","required_roles":["founder","implementer","tester"],"revision":"'
                  b'current"},"author":{"artifact":"immutable-prd-revision","kind":"agent","role'
                  b'":"product-lead"},"description":"Immutable PRD authoring and current-revisio'
                  b'n review","kind":"product-design","request_changes":{"action":"return-to-aut'
                  b'hor"},"reviewers":[{"kind":"human","role":"founder"},{"kind":"agent","role":'
                  b'"implementer"},{"kind":"agent","role":"tester"}],"schema_version":1}}',
 'context_digest': 'c09237e3849d1c662dc079d3ce1ec148508ca8716ecf9140e709c2bf570e4f68',
 'context_id': 'workflow-context:12e2cd46aad5ead9e871fe2a9e83845d98312d1c4edc5291b045b0c71d42497d',
 'event_bytes': b'{"after":{"cancellation_requested":0,"claim_owner":null,"claim_token":null,"fina'
                b'l_result_id":null,"host_execution_id":null,"host_launch_started":0,"is_current":'
                b'1,"session_id":null,"state":"queued"},"before":null,"event":{"created_at":"2026-'
                b'10-01T00:00:00+00:00","event_kind":"admitted","event_seq":1,"id":"f019d3c21791ae'
                b'e69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0:1"},"format":"workflow-draft-'
                b'event@1","intent":{"activation_id":"workflow-activation:7dbd124ce2ff79975d018ce4'
                b'0e27bc93fc8814c0fda239fc356da8e21f90a044","activation_revision":1,"admission_kin'
                b'd":"initial","admission_principal":"human:founder","assigned_principal":"product'
                b'_lead","assignment_generation":1,"attempt_sequence":1,"authority_digest":"1748e8'
                b'93628dc107fa884d2cf44f5a69fcd5f7d69e5754042bf19adb0ee9ad44","authority_generatio'
                b'n":1,"authority_namespace":"org/alpha/team/product","binding_snapshot_id":"workf'
                b'low-binding:c365db4c52d8530246b4ed14d2e017f633b66b4b7479c84693402a5723b1d91c","c'
                b'ontext_id":"workflow-context:12e2cd46aad5ead9e871fe2a9e83845d98312d1c4edc5291b04'
                b'5b0c71d42497d","created_at":"2026-10-01T00:00:00+00:00","effect_key":"workflow-i'
                b'nitial-draft:workflow-instance:360939a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31da'
                b'b5635ec286d2858:1","host_execution_key":"workflow-draft-host:f019d3c21791aee69dc'
                b'fc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0","id":"f019d3c21791aee69dcfc18e700'
                b'a7d76fce8c1a92d1caf63c7152b18d38ba5d0","instance_id":"workflow-instance:360939a9'
                b'0b15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635ec286d2858","operation_key":"c5-re'
                b'tained-schema1","predecessor_intent_id":null,"recovery_owner":"workflow_recovery'
                b'","request_bytes":"7b22616c6c6f7765645f616374696f6e73223a5b2264726166742d646f637'
                b'56d656e74222c227375626d69742d696d6d757461626c652d646f63756d656e74222c22636f6c6c6'
                b'563742d726576696577222c22617070726f76652d706c616e6e696e672d696e707574222c2272657'
                b'475726e2d746f2d617574686f72225d2c22617574686f72697479223a7b2267656e65726174696f6'
                b'e223a312c226e616d657370616365223a226f72672f616c706861222c22736e617073686f745f646'
                b'967657374223a2231373438653839333632386463313037666138383464326366343466356136396'
                b'663643566376436396535373534303432626631396164623065653961643434227d2c2262696e646'
                b'96e6773223a7b22666f756e646572223a7b226b696e64223a2268756d616e222c227072696e63697'
                b'0616c223a22666f756e646572222c227465616d223a6e756c6c7d2c22696d706c656d656e7465722'
                b'23a7b226b696e64223a226167656e74222c227072696e636970616c223a226465765f6167656e742'
                b'22c227465616d223a22656e67696e656572696e67227d2c2270726f647563742d6c656164223a7b2'
                b'26b696e64223a226167656e74222c227072696e636970616c223a2270726f647563745f6c6561642'
                b'22c227465616d223a2270726f64756374227d2c22746573746572223a7b226b696e64223a2261676'
                b'56e74222c227072696e636970616c223a2271615f656e67696e656572222c227465616d223a22656'
                b'e67696e656572696e67227d7d2c22656c696769626c655f7265706c6163656d656e7473223a7b226'
                b'66f756e646572223a5b5d2c22696d706c656d656e746572223a5b5d2c2270726f647563742d6c656'
                b'164223a5b5d2c22746573746572223a5b5d7d2c2265787065637465645f61637469766174696f6e5'
                b'f7265766973696f6e223a302c22696e70757473223a5b5d2c22696e7374616e63655f6964223a226'
                b'3352d72657461696e65642d736368656d6131222c226f7065726174696f6e5f6b6579223a2263352'
                b'd72657461696e65642d736368656d6131222c2273636f7065223a7b226272696566223a225265746'
                b'1696e656420626f756e64656420736368656d613120646f63756d656e742e227d2c2274656d706c6'
                b'17465223a7b22646566696e6974696f6e5f646967657374223a22306438313534393738393964316'
                b'53666343330323265633863383835373461333662626632393561303566626630306634313163386'
                b'138653332346337663537222c226964656e746974795f6964223a22776f726b666c6f772d74656d7'
                b'06c6174653a303732373263376663353630313438626434613833616566323366643831303063366'
                b'66338616334626636346265656630616637373934623261376363396132222c2276657273696f6e2'
                b'23a317d7d","request_digest":"3f5e27e1c845b676e5defbfc64b7cd7fbd2f3cb57527271fe70'
                b'f761db483238f","task_id":"TASK-900","task_scope_bytes":"7b2261737369676e65645f61'
                b'67656e74223a2270726f647563745f6c656164222c226272696566223a22576f726b666c6f772069'
                b'6e697469616c206472616674696e67207461736b2e2050726f64756365206120626f756e64656420'
                b'646f63756d656e74206172746966616374206f6e6c792e20446f206e6f742064656c65676174652c'
                b'2066616e206f75742c20696d706c656d656e7420636f64652c206d657267652c206f722061707072'
                b'6f76652074686520646f63756d656e742e20436f6d706c6574696f6e207072657365727665732061'
                b'2064726166743b20696d6d757461626c65207375626d697373696f6e2f7265766965777320617265'
                b'206c6174657220756e6974732e5c6e5c6e52657461696e656420626f756e64656420736368656d61'
                b'3120646f63756d656e742e222c227465616d223a2270726f64756374227d","task_scope_digest'
                b'":"3e1b507762a21bb4e2f85e78abfe41e73f38dba8d356d09a92cca586a710d0e2"},"org_slug"'
                b':"alpha","previous_digest":null,"result":null,"terminal_evidence":null}',
 'event_digest': '9805dd9a0b61873f08f77293250b15ca06492dd5cfe6c359b7afe914af3e3e2d',
 'intent': {'activation_id': 'workflow-activation:7dbd124ce2ff79975d018ce40e27bc93fc8814c0fda239fc356da8e21f90a044',
            'activation_revision': 1,
            'admission_kind': 'initial',
            'admission_principal': 'human:founder',
            'assigned_principal': 'product_lead',
            'assignment_generation': 1,
            'attempt_sequence': 1,
            'authority_digest': '1748e893628dc107fa884d2cf44f5a69fcd5f7d69e5754042bf19adb0ee9ad44',
            'authority_generation': 1,
            'authority_namespace': 'org/alpha/team/product',
            'binding_snapshot_id': 'workflow-binding:c365db4c52d8530246b4ed14d2e017f633b66b4b7479c84693402a5723b1d91c',
            'cancellation_requested': 0,
            'claim_owner': None,
            'claim_token': None,
            'context_id': 'workflow-context:12e2cd46aad5ead9e871fe2a9e83845d98312d1c4edc5291b045b0c71d42497d',
            'created_at': '2026-10-01T00:00:00+00:00',
            'effect_key': 'workflow-initial-draft:workflow-instance:360939a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635ec286d2858:1',
            'final_result_id': None,
            'host_execution_id': None,
            'host_execution_key': 'workflow-draft-host:f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0',
            'host_launch_started': 0,
            'id': 'f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0',
            'instance_id': 'workflow-instance:360939a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635ec286d2858',
            'is_current': 1,
            'last_error': None,
            'operation_key': 'c5-retained-schema1',
            'predecessor_intent_id': None,
            'recovery_owner': 'workflow_recovery',
            'request_bytes': b'{"allowed_actions":["draft-document","submit-immutable-document","co'
                             b'llect-review","approve-planning-input","return-to-author"],"authorit'
                             b'y":{"generation":1,"namespace":"org/alpha","snapshot_digest":"1748e8'
                             b'93628dc107fa884d2cf44f5a69fcd5f7d69e5754042bf19adb0ee9ad44"},"bindin'
                             b'gs":{"founder":{"kind":"human","principal":"founder","team":null},"i'
                             b'mplementer":{"kind":"agent","principal":"dev_agent","team":"engineer'
                             b'ing"},"product-lead":{"kind":"agent","principal":"product_lead","tea'
                             b'm":"product"},"tester":{"kind":"agent","principal":"qa_engineer","te'
                             b'am":"engineering"}},"eligible_replacements":{"founder":[],"implement'
                             b'er":[],"product-lead":[],"tester":[]},"expected_activation_revision"'
                             b':0,"inputs":[],"instance_id":"c5-retained-schema1","operation_key":"'
                             b'c5-retained-schema1","scope":{"brief":"Retained bounded schema1 docu'
                             b'ment."},"template":{"definition_digest":"0d815497899d1e6f43022ec8c88'
                             b'574a36bbf295a05fbf00f411c8a8e324c7f57","identity_id":"workflow-templ'
                             b'ate:07272c7fc560148bd4a83aef23fd8100c6fc8ac4bf64beef0af7794b2a7cc9a2'
                             b'","version":1}}',
            'request_digest': '3f5e27e1c845b676e5defbfc64b7cd7fbd2f3cb57527271fe70f761db483238f',
            'session_id': None,
            'state': 'queued',
            'task_id': 'TASK-900',
            'task_scope_bytes': b'{"assigned_agent":"product_lead","brief":"Workflow initial draft'
                                b'ing task. Produce a bounded document artifact only. Do not deleg'
                                b'ate, fan out, implement code, merge, or approve the document. Co'
                                b'mpletion preserves a draft; immutable submission/reviews are lat'
                                b'er units.\\n\\nRetained bounded schema1 document.","team":"pro'
                                b'duct"}',
            'task_scope_digest': '3e1b507762a21bb4e2f85e78abfe41e73f38dba8d356d09a92cca586a710d0e2',
            'updated_at': '2026-10-01T00:00:00+00:00'},
 'receipt': {'activated_by': {'principal_id': 'founder',
                              'principal_kind': 'human',
                              'proof_kind': 'founder_bearer'},
             'activation_id': 'workflow-activation:7dbd124ce2ff79975d018ce40e27bc93fc8814c0fda239fc356da8e21f90a044',
             'activation_revision': 1,
             'allowed_actions': ['draft-document',
                                 'submit-immutable-document',
                                 'collect-review',
                                 'approve-planning-input',
                                 'return-to-author'],
             'authority': {'generation': 1,
                           'namespace': 'org/alpha',
                           'snapshot_digest': '1748e893628dc107fa884d2cf44f5a69fcd5f7d69e5754042bf19adb0ee9ad44'},
             'bindings': {'founder': {'kind': 'human', 'principal': 'founder', 'team': None},
                          'implementer': {'kind': 'agent',
                                          'principal': 'dev_agent',
                                          'team': 'engineering'},
                          'product-lead': {'kind': 'agent',
                                           'principal': 'product_lead',
                                           'team': 'product'},
                          'tester': {'kind': 'agent',
                                     'principal': 'qa_engineer',
                                     'team': 'engineering'}},
             'context_digest': 'c09237e3849d1c662dc079d3ce1ec148508ca8716ecf9140e709c2bf570e4f68',
             'created_at': '2026-10-01T00:00:00+00:00',
             'eligible_replacements': {'founder': [],
                                       'implementer': [],
                                       'product-lead': [],
                                       'tester': []},
             'instance_id': 'workflow-instance:360939a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635ec286d2858',
             'instance_reference': 'c5-retained-schema1',
             'intent_id': 'f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0',
             'original_request_digest': '3f5e27e1c845b676e5defbfc64b7cd7fbd2f3cb57527271fe70f761db483238f',
             'root_task_id': 'TASK-900',
             'scope_digest': 'bd3a776802bf9620f9279d27d585d5438a567e41d4a0d35ce2632ea345e653e6',
             'template': {'compiler_pin': 'workflow-compiler@1',
                          'definition_digest': '0d815497899d1e6f43022ec8c88574a36bbf295a05fbf00f411c8a8e324c7f57',
                          'identity_id': 'workflow-template:07272c7fc560148bd4a83aef23fd8100c6fc8ac4bf64beef0af7794b2a7cc9a2',
                          'source_pin': 'operator-input@1',
                          'validator_pin': 'workflow-validator@1',
                          'version': 1,
                          'version_id': 'workflow-template-version:73a8d146c3aeef70da5129392093488d903f8ee053f287763417deb51b8c88cd'}},
 'request': {'allowed_actions': ['draft-document',
                                 'submit-immutable-document',
                                 'collect-review',
                                 'approve-planning-input',
                                 'return-to-author'],
             'authority': {'generation': 1,
                           'namespace': 'org/alpha',
                           'snapshot_digest': '1748e893628dc107fa884d2cf44f5a69fcd5f7d69e5754042bf19adb0ee9ad44'},
             'bindings': {'founder': {'kind': 'human', 'principal': 'founder', 'team': None},
                          'implementer': {'kind': 'agent',
                                          'principal': 'dev_agent',
                                          'team': 'engineering'},
                          'product-lead': {'kind': 'agent',
                                           'principal': 'product_lead',
                                           'team': 'product'},
                          'tester': {'kind': 'agent',
                                     'principal': 'qa_engineer',
                                     'team': 'engineering'}},
             'eligible_replacements': {'founder': [],
                                       'implementer': [],
                                       'product-lead': [],
                                       'tester': []},
             'expected_activation_revision': 0,
             'inputs': [],
             'instance_id': 'c5-retained-schema1',
             'operation_key': 'c5-retained-schema1',
             'scope': {'brief': 'Retained bounded schema1 document.'},
             'template': {'definition_digest': '0d815497899d1e6f43022ec8c88574a36bbf295a05fbf00f411c8a8e324c7f57',
                          'identity_id': 'workflow-template:07272c7fc560148bd4a83aef23fd8100c6fc8ac4bf64beef0af7794b2a7cc9a2',
                          'version': 1}},
 'snapshot_bytes': b'{"active_policy_selectors":[{"selector":null,"team":"engineering"},{"selecto'
                   b'r":null,"team":"product"}],"agents":[{"allow_rules":[],"description":"Retain'
                   b'ed schema1 fixture","executor":"claude","model":null,"name":"code_reviewer",'
                   b'"repos":{},"role":"worker","status":"active","system_prompt_digest":"3608eb7'
                   b'a99d51e5e5cb67d90ae3ea066d6bec944052341fe34c0be3dcef315d8","team":"engineeri'
                   b'ng"},{"allow_rules":[],"description":"Retained schema1 fixture","executor":"'
                   b'claude","model":null,"name":"dev_agent","repos":{},"role":"worker","status":'
                   b'"active","system_prompt_digest":"3608eb7a99d51e5e5cb67d90ae3ea066d6bec944052'
                   b'341fe34c0be3dcef315d8","team":"engineering"},{"allow_rules":[],"description"'
                   b':"Retained schema1 fixture","executor":"claude","model":null,"name":"enginee'
                   b'ring_manager","repos":{},"role":"manager","status":"active","system_prompt_d'
                   b'igest":"3608eb7a99d51e5e5cb67d90ae3ea066d6bec944052341fe34c0be3dcef315d8","t'
                   b'eam":"engineering"},{"allow_rules":[],"description":"Retained schema1 fixtur'
                   b'e","executor":"claude","model":null,"name":"product_lead","repos":{},"role":'
                   b'"manager","status":"active","system_prompt_digest":"3608eb7a99d51e5e5cb67d90'
                   b'ae3ea066d6bec944052341fe34c0be3dcef315d8","team":"product"},{"allow_rules":['
                   b'],"description":"Retained schema1 fixture","executor":"claude","model":null,'
                   b'"name":"qa_engineer","repos":{},"role":"worker","status":"active","system_pr'
                   b'ompt_digest":"3608eb7a99d51e5e5cb67d90ae3ea066d6bec944052341fe34c0be3dcef315'
                   b'd8","team":"engineering"}],"machine_global_profiles":[],"org_slug":"alpha","'
                   b'reviewer_agents":["code_reviewer"],"schema_version":1,"teams":[{"manager":"e'
                   b'ngineering_manager","name":"engineering","workers":["code_reviewer","dev_age'
                   b'nt","qa_engineer"]},{"manager":"product_lead","name":"product","workers":[]}'
                   b']}',
 'snapshot_digest': '1748e893628dc107fa884d2cf44f5a69fcd5f7d69e5754042bf19adb0ee9ad44',
 'template_version_id': 'workflow-template-version:73a8d146c3aeef70da5129392093488d903f8ee053f287763417deb51b8c88cd',
 'timestamp': '2026-10-01T00:00:00+00:00'}


def seed_c5_schema1_history(org, *, completed=False):
    """Install fixed HISTORICAL DATA in the existing fixture owner.

    All callback/host/result values in the completed variant are fixed historical
    fixture DATA, never an executed callback, host or migration receipt. The
    retained journal is not the current pointer. New schema2 graphs must come
    from actual activate(), and receipt interpretation comes from get/replay.
    """
    import hashlib
    from runtime.models import TaskRecord
    from runtime.infrastructure.workflow_schema import validate_workflow_schema

    f = C5_SCHEMA1_HISTORY
    for label in ('snapshot', 'authorization', 'binding', 'context', 'event'):
        assert hashlib.sha256(f[label + '_bytes']).hexdigest() == f[label + '_digest']
    assert org.slug == 'alpha'
    receipt = f['receipt']
    pin = receipt['template']
    assert org.db.execute('SELECT definition_digest,compiler_pin,validator_pin,source_pin FROM workflow_template_versions WHERE id=?',
                          (pin['version_id'],)).fetchone()[:] == tuple(pin[key] for key in ('definition_digest','compiler_pin','validator_pin','source_pin'))
    with org.db._lock:
        conn = org.db._conn
        conn.execute('BEGIN IMMEDIATE')
        try:
            org.db._insert_task_uncommitted(TaskRecord(id=receipt['root_task_id'], assigned_agent='product_lead',
                team='product', brief=f['brief'], task_type='subtask'))
            conn.execute('INSERT INTO workflow_publication_journals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                ('c5-fixed-schema1-history', 'org/alpha', 1, 0, f['snapshot_bytes'], f['snapshot_digest'],
                 'c5-historical-fixture', 'c5-historical-fixture', 0, 'cache_installed', 'workflow_publication_recovery', None))
            conn.execute('INSERT INTO workflow_authorization_revisions VALUES (?,?,?,?,?,?,?)',
                (f['authorization_id'], 'org/alpha/workflow-instance/' + receipt['instance_id'], 1,
                 f['authorization_bytes'], f['authorization_digest'], pin['source_pin'], f['timestamp']))
            conn.execute('INSERT INTO workflow_active_authorizations VALUES (?,?)',
                ('org/alpha/workflow-instance/' + receipt['instance_id'], f['authorization_id']))
            conn.execute('INSERT INTO workflow_binding_snapshots VALUES (?,?,?,?,?,?)',
                (f['binding_id'], pin['version_id'], f['authorization_id'], f['binding_bytes'], f['binding_digest'], f['timestamp']))
            conn.execute('INSERT INTO workflow_contexts VALUES (?,?,?,?,?,?)',
                (f['context_id'], f['binding_id'], f['context_bytes'], f['context_digest'], 'founder-activation', receipt['instance_id']))
            conn.execute('INSERT INTO workflow_instances VALUES (?,?,?,?,?,?)',
                (receipt['instance_id'], f['binding_id'], f['context_id'], receipt['root_task_id'], 'human:founder', 'draft'))
            conn.execute('INSERT INTO workflow_activations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (receipt['activation_id'], receipt['instance_id'], 1, pin['identity_id'], pin['version_id'],
                 'org/alpha/team/product', 1, f['snapshot_digest'], receipt['original_request_digest'], 'human:founder', 'active', f['timestamp']))
            conn.execute('INSERT INTO workflow_active_activations VALUES (?,?,?)', (receipt['instance_id'],receipt['activation_id'],1))
            conn.execute('INSERT INTO workflow_activation_operations VALUES (?,?,?,?,?)',
                ('alpha','human:founder',f['request']['operation_key'],receipt['original_request_digest'],receipt['activation_id']))
            intent = {**f['intent'], **(C5_SCHEMA1_COMPLETED['projection'] if completed else {})}
            if completed:
                record = C5_SCHEMA1_COMPLETED['result']
                conn.execute('INSERT INTO task_results (' + ','.join(record) + ') VALUES (' + ','.join('?' for _ in record) + ')', tuple(record.values()))
                conn.execute("UPDATE tasks SET status='completed',current_session_id=?,completed_at=? WHERE id=?", (record['session_id'], f['timestamp'], record['task_id']))
            conn.execute('INSERT INTO workflow_draft_dispatch_intents (' + ','.join(intent) + ') VALUES (' + ','.join('?' for _ in intent) + ')', tuple(intent.values()))
            if completed:
                for event in C5_SCHEMA1_COMPLETED['events']:
                    conn.execute('INSERT INTO workflow_draft_dispatch_events (' + ','.join(event) + ') VALUES (' + ','.join('?' for _ in event) + ')', tuple(event.values()))
            else:
                conn.execute('INSERT INTO workflow_draft_dispatch_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (receipt['intent_id'] + ':1',receipt['intent_id'],1,'admitted',None,'queued',f['event_bytes'],f['event_digest'],None,None,None,None,None,f['timestamp']))
            validate_workflow_schema(conn, expected_org_slug='alpha')
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return f


def c5_profile_fixture(name):
    """Reuse the existing profile owner with its real durable registration."""
    from contextlib import contextmanager
    from runtime.orchestrator.runtime_executor_store import load_runtime_profiles, save_runtime_profile, remove_runtime_profile
    from tests.workflows.test_profile_coordinator import _registered_profile
    @contextmanager
    def registered():
        prior = load_runtime_profiles().get(name)
        save_runtime_profile(name, {'workspace_adapter_id':'pi','command_adapter_id':f'custom-adapter:{name}-adapter'})
        try:
            with _registered_profile(name) as profile:
                yield profile
        finally:
            if prior is None: remove_runtime_profile(name)
            else: save_runtime_profile(name,prior)
    return registered()


# Retained completed-history fixture DATA, not an executed callback/host proof.
C5_SCHEMA1_COMPLETED = {'events': [{'callback_accepted': None,
             'created_at': '2026-10-01T00:00:00+00:00',
             'disposition': None,
             'event_bytes': b'{"after":{"cancellation_requested":0,"claim_owner":null,"claim_token'
                            b'":null,"final_result_id":null,"host_execution_id":null,"host_launch_'
                            b'started":0,"is_current":1,"session_id":null,"state":"queued"},"befor'
                            b'e":null,"event":{"created_at":"2026-10-01T00:00:00+00:00","event_kin'
                            b'd":"admitted","event_seq":1,"id":"f019d3c21791aee69dcfc18e700a7d76fc'
                            b'e8c1a92d1caf63c7152b18d38ba5d0:1"},"format":"workflow-draft-event@1"'
                            b',"intent":{"activation_id":"workflow-activation:7dbd124ce2ff79975d01'
                            b'8ce40e27bc93fc8814c0fda239fc356da8e21f90a044","activation_revision":'
                            b'1,"admission_kind":"initial","admission_principal":"human:founder","'
                            b'assigned_principal":"product_lead","assignment_generation":1,"attemp'
                            b't_sequence":1,"authority_digest":"1748e893628dc107fa884d2cf44f5a69fc'
                            b'd5f7d69e5754042bf19adb0ee9ad44","authority_generation":1,"authority_'
                            b'namespace":"org/alpha/team/product","binding_snapshot_id":"workflow-'
                            b'binding:c365db4c52d8530246b4ed14d2e017f633b66b4b7479c84693402a5723b1'
                            b'd91c","context_id":"workflow-context:12e2cd46aad5ead9e871fe2a9e83845'
                            b'd98312d1c4edc5291b045b0c71d42497d","created_at":"2026-10-01T00:00:00'
                            b'+00:00","effect_key":"workflow-initial-draft:workflow-instance:36093'
                            b'9a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635ec286d2858:1","host'
                            b'_execution_key":"workflow-draft-host:f019d3c21791aee69dcfc18e700a7d7'
                            b'6fce8c1a92d1caf63c7152b18d38ba5d0","id":"f019d3c21791aee69dcfc18e700'
                            b'a7d76fce8c1a92d1caf63c7152b18d38ba5d0","instance_id":"workflow-insta'
                            b'nce:360939a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635ec286d2858'
                            b'","operation_key":"c5-retained-schema1","predecessor_intent_id":null'
                            b',"recovery_owner":"workflow_recovery","request_bytes":"7b22616c6c6f7'
                            b'765645f616374696f6e73223a5b2264726166742d646f63756d656e74222c2273756'
                            b'26d69742d696d6d757461626c652d646f63756d656e74222c22636f6c6c6563742d7'
                            b'26576696577222c22617070726f76652d706c616e6e696e672d696e707574222c227'
                            b'2657475726e2d746f2d617574686f72225d2c22617574686f72697479223a7b22676'
                            b'56e65726174696f6e223a312c226e616d657370616365223a226f72672f616c70686'
                            b'1222c22736e617073686f745f646967657374223a223137343865383933363238646'
                            b'33130376661383834643263663434663561363966636435663764363965353735343'
                            b'03432626631396164623065653961643434227d2c2262696e64696e6773223a7b226'
                            b'66f756e646572223a7b226b696e64223a2268756d616e222c227072696e636970616'
                            b'c223a22666f756e646572222c227465616d223a6e756c6c7d2c22696d706c656d656'
                            b'e746572223a7b226b696e64223a226167656e74222c227072696e636970616c223a2'
                            b'26465765f6167656e74222c227465616d223a22656e67696e656572696e67227d2c2'
                            b'270726f647563742d6c656164223a7b226b696e64223a226167656e74222c2270726'
                            b'96e636970616c223a2270726f647563745f6c656164222c227465616d223a2270726'
                            b'f64756374227d2c22746573746572223a7b226b696e64223a226167656e74222c227'
                            b'072696e636970616c223a2271615f656e67696e656572222c227465616d223a22656'
                            b'e67696e656572696e67227d7d2c22656c696769626c655f7265706c6163656d656e7'
                            b'473223a7b22666f756e646572223a5b5d2c22696d706c656d656e746572223a5b5d2'
                            b'c2270726f647563742d6c656164223a5b5d2c22746573746572223a5b5d7d2c22657'
                            b'87065637465645f61637469766174696f6e5f7265766973696f6e223a302c22696e7'
                            b'0757473223a5b5d2c22696e7374616e63655f6964223a2263352d72657461696e656'
                            b'42d736368656d6131222c226f7065726174696f6e5f6b6579223a2263352d7265746'
                            b'1696e65642d736368656d6131222c2273636f7065223a7b226272696566223a22526'
                            b'57461696e656420626f756e64656420736368656d613120646f63756d656e742e227'
                            b'd2c2274656d706c617465223a7b22646566696e6974696f6e5f646967657374223a2'
                            b'23064383135343937383939643165366634333032326563386338383537346133366'
                            b'2626632393561303566626630306634313163386138653332346337663537222c226'
                            b'964656e746974795f6964223a22776f726b666c6f772d74656d706c6174653a30373'
                            b'23732633766633536303134386264346138336165663233666438313030633666633'
                            b'8616334626636346265656630616637373934623261376363396132222c227665727'
                            b'3696f6e223a317d7d","request_digest":"3f5e27e1c845b676e5defbfc64b7cd7'
                            b'fbd2f3cb57527271fe70f761db483238f","task_id":"TASK-900","task_scope_'
                            b'bytes":"7b2261737369676e65645f6167656e74223a2270726f647563745f6c6561'
                            b'64222c226272696566223a22576f726b666c6f7720696e697469616c206472616674'
                            b'696e67207461736b2e2050726f64756365206120626f756e64656420646f63756d65'
                            b'6e74206172746966616374206f6e6c792e20446f206e6f742064656c65676174652c'
                            b'2066616e206f75742c20696d706c656d656e7420636f64652c206d657267652c206f'
                            b'7220617070726f76652074686520646f63756d656e742e20436f6d706c6574696f6e'
                            b'2070726573657276657320612064726166743b20696d6d757461626c65207375626d'
                            b'697373696f6e2f7265766965777320617265206c6174657220756e6974732e5c6e5c'
                            b'6e52657461696e656420626f756e64656420736368656d613120646f63756d656e74'
                            b'2e222c227465616d223a2270726f64756374227d","task_scope_digest":"3e1b5'
                            b'07762a21bb4e2f85e78abfe41e73f38dba8d356d09a92cca586a710d0e2"},"org_s'
                            b'lug":"alpha","previous_digest":null,"result":null,"terminal_evidence'
                            b'":null}',
             'event_digest': '9805dd9a0b61873f08f77293250b15ca06492dd5cfe6c359b7afe914af3e3e2d',
             'event_kind': 'admitted',
             'event_seq': 1,
             'id': 'f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0:1',
             'intent_id': 'f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0',
             'result_digest': None,
             'result_id': None,
             'session_id': None,
             'state_after': 'queued',
             'state_before': None},
            {'callback_accepted': None,
             'created_at': '2026-10-01T00:00:00+00:00',
             'disposition': None,
             'event_bytes': b'{"after":{"cancellation_requested":0,"claim_owner":"workflow_dispatc'
                            b'her","claim_token":"c5-fixed-claim","final_result_id":null,"host_exe'
                            b'cution_id":null,"host_launch_started":0,"is_current":1,"session_id":'
                            b'null,"state":"claimed"},"before":{"cancellation_requested":0,"claim_'
                            b'owner":null,"claim_token":null,"final_result_id":null,"host_executio'
                            b'n_id":null,"host_launch_started":0,"is_current":1,"session_id":null,'
                            b'"state":"queued"},"event":{"created_at":"2026-10-01T00:00:00+00:00",'
                            b'"event_kind":"claimed","event_seq":2,"id":"f019d3c21791aee69dcfc18e7'
                            b'00a7d76fce8c1a92d1caf63c7152b18d38ba5d0:2"},"format":"workflow-draft'
                            b'-event@1","intent":{"activation_id":"workflow-activation:7dbd124ce2f'
                            b'f79975d018ce40e27bc93fc8814c0fda239fc356da8e21f90a044","activation_r'
                            b'evision":1,"admission_kind":"initial","admission_principal":"human:f'
                            b'ounder","assigned_principal":"product_lead","assignment_generation":'
                            b'1,"attempt_sequence":1,"authority_digest":"1748e893628dc107fa884d2cf'
                            b'44f5a69fcd5f7d69e5754042bf19adb0ee9ad44","authority_generation":1,"a'
                            b'uthority_namespace":"org/alpha/team/product","binding_snapshot_id":"'
                            b'workflow-binding:c365db4c52d8530246b4ed14d2e017f633b66b4b7479c846934'
                            b'02a5723b1d91c","context_id":"workflow-context:12e2cd46aad5ead9e871fe'
                            b'2a9e83845d98312d1c4edc5291b045b0c71d42497d","created_at":"2026-10-01'
                            b'T00:00:00+00:00","effect_key":"workflow-initial-draft:workflow-insta'
                            b'nce:360939a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635ec286d2858'
                            b':1","host_execution_key":"workflow-draft-host:f019d3c21791aee69dcfc1'
                            b'8e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0","id":"f019d3c21791aee69d'
                            b'cfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0","instance_id":"workf'
                            b'low-instance:360939a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635e'
                            b'c286d2858","operation_key":"c5-retained-schema1","predecessor_intent'
                            b'_id":null,"recovery_owner":"workflow_recovery","request_bytes":"7b22'
                            b'616c6c6f7765645f616374696f6e73223a5b2264726166742d646f63756d656e7422'
                            b'2c227375626d69742d696d6d757461626c652d646f63756d656e74222c22636f6c6c'
                            b'6563742d726576696577222c22617070726f76652d706c616e6e696e672d696e7075'
                            b'74222c2272657475726e2d746f2d617574686f72225d2c22617574686f7269747922'
                            b'3a7b2267656e65726174696f6e223a312c226e616d657370616365223a226f72672f'
                            b'616c706861222c22736e617073686f745f646967657374223a223137343865383933'
                            b'36323864633130376661383834643263663434663561363966636435663764363965'
                            b'35373534303432626631396164623065653961643434227d2c2262696e64696e6773'
                            b'223a7b22666f756e646572223a7b226b696e64223a2268756d616e222c227072696e'
                            b'636970616c223a22666f756e646572222c227465616d223a6e756c6c7d2c22696d70'
                            b'6c656d656e746572223a7b226b696e64223a226167656e74222c227072696e636970'
                            b'616c223a226465765f6167656e74222c227465616d223a22656e67696e656572696e'
                            b'67227d2c2270726f647563742d6c656164223a7b226b696e64223a226167656e7422'
                            b'2c227072696e636970616c223a2270726f647563745f6c656164222c227465616d22'
                            b'3a2270726f64756374227d2c22746573746572223a7b226b696e64223a226167656e'
                            b'74222c227072696e636970616c223a2271615f656e67696e656572222c227465616d'
                            b'223a22656e67696e656572696e67227d7d2c22656c696769626c655f7265706c6163'
                            b'656d656e7473223a7b22666f756e646572223a5b5d2c22696d706c656d656e746572'
                            b'223a5b5d2c2270726f647563742d6c656164223a5b5d2c22746573746572223a5b5d'
                            b'7d2c2265787065637465645f61637469766174696f6e5f7265766973696f6e223a30'
                            b'2c22696e70757473223a5b5d2c22696e7374616e63655f6964223a2263352d726574'
                            b'61696e65642d736368656d6131222c226f7065726174696f6e5f6b6579223a226335'
                            b'2d72657461696e65642d736368656d6131222c2273636f7065223a7b226272696566'
                            b'223a2252657461696e656420626f756e64656420736368656d613120646f63756d65'
                            b'6e742e227d2c2274656d706c617465223a7b22646566696e6974696f6e5f64696765'
                            b'7374223a223064383135343937383939643165366634333032326563386338383537'
                            b'34613336626266323935613035666266303066343131633861386533323463376635'
                            b'37222c226964656e746974795f6964223a22776f726b666c6f772d74656d706c6174'
                            b'653a3037323732633766633536303134386264346138336165663233666438313030'
                            b'6336666338616334626636346265656630616637373934623261376363396132222c'
                            b'2276657273696f6e223a317d7d","request_digest":"3f5e27e1c845b676e5defb'
                            b'fc64b7cd7fbd2f3cb57527271fe70f761db483238f","task_id":"TASK-900","ta'
                            b'sk_scope_bytes":"7b2261737369676e65645f6167656e74223a2270726f6475637'
                            b'45f6c656164222c226272696566223a22576f726b666c6f7720696e697469616c206'
                            b'472616674696e67207461736b2e2050726f64756365206120626f756e64656420646'
                            b'f63756d656e74206172746966616374206f6e6c792e20446f206e6f742064656c656'
                            b'76174652c2066616e206f75742c20696d706c656d656e7420636f64652c206d65726'
                            b'7652c206f7220617070726f76652074686520646f63756d656e742e20436f6d706c6'
                            b'574696f6e2070726573657276657320612064726166743b20696d6d757461626c652'
                            b'07375626d697373696f6e2f7265766965777320617265206c6174657220756e69747'
                            b'32e5c6e5c6e52657461696e656420626f756e64656420736368656d613120646f637'
                            b'56d656e742e222c227465616d223a2270726f64756374227d","task_scope_diges'
                            b't":"3e1b507762a21bb4e2f85e78abfe41e73f38dba8d356d09a92cca586a710d0e2'
                            b'"},"org_slug":"alpha","previous_digest":"9805dd9a0b61873f08f77293250'
                            b'b15ca06492dd5cfe6c359b7afe914af3e3e2d","result":null,"terminal_evide'
                            b'nce":null}',
             'event_digest': 'f71d9486ad96c2a4d1222c97af6c0c92c20afae8d67a8f38bff91b40656f5889',
             'event_kind': 'claimed',
             'event_seq': 2,
             'id': 'f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0:2',
             'intent_id': 'f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0',
             'result_digest': None,
             'result_id': None,
             'session_id': None,
             'state_after': 'claimed',
             'state_before': 'queued'},
            {'callback_accepted': None,
             'created_at': '2026-10-01T00:00:00+00:00',
             'disposition': None,
             'event_bytes': b'{"after":{"cancellation_requested":0,"claim_owner":"workflow_dispatc'
                            b'her","claim_token":"c5-fixed-claim","final_result_id":null,"host_exe'
                            b'cution_id":null,"host_launch_started":1,"is_current":1,"session_id":'
                            b'"sess-c5-historical","state":"claimed"},"before":{"cancellation_requ'
                            b'ested":0,"claim_owner":"workflow_dispatcher","claim_token":"c5-fixed'
                            b'-claim","final_result_id":null,"host_execution_id":null,"host_launch'
                            b'_started":0,"is_current":1,"session_id":null,"state":"claimed"},"eve'
                            b'nt":{"created_at":"2026-10-01T00:00:00+00:00","event_kind":"launch_r'
                            b'eserved","event_seq":3,"id":"f019d3c21791aee69dcfc18e700a7d76fce8c1a'
                            b'92d1caf63c7152b18d38ba5d0:3"},"format":"workflow-draft-event@1","int'
                            b'ent":{"activation_id":"workflow-activation:7dbd124ce2ff79975d018ce40'
                            b'e27bc93fc8814c0fda239fc356da8e21f90a044","activation_revision":1,"ad'
                            b'mission_kind":"initial","admission_principal":"human:founder","assig'
                            b'ned_principal":"product_lead","assignment_generation":1,"attempt_seq'
                            b'uence":1,"authority_digest":"1748e893628dc107fa884d2cf44f5a69fcd5f7d'
                            b'69e5754042bf19adb0ee9ad44","authority_generation":1,"authority_names'
                            b'pace":"org/alpha/team/product","binding_snapshot_id":"workflow-bindi'
                            b'ng:c365db4c52d8530246b4ed14d2e017f633b66b4b7479c84693402a5723b1d91c"'
                            b',"context_id":"workflow-context:12e2cd46aad5ead9e871fe2a9e83845d9831'
                            b'2d1c4edc5291b045b0c71d42497d","created_at":"2026-10-01T00:00:00+00:0'
                            b'0","effect_key":"workflow-initial-draft:workflow-instance:360939a90b'
                            b'15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635ec286d2858:1","host_exec'
                            b'ution_key":"workflow-draft-host:f019d3c21791aee69dcfc18e700a7d76fce8'
                            b'c1a92d1caf63c7152b18d38ba5d0","id":"f019d3c21791aee69dcfc18e700a7d76'
                            b'fce8c1a92d1caf63c7152b18d38ba5d0","instance_id":"workflow-instance:3'
                            b'60939a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635ec286d2858","op'
                            b'eration_key":"c5-retained-schema1","predecessor_intent_id":null,"rec'
                            b'overy_owner":"workflow_recovery","request_bytes":"7b22616c6c6f776564'
                            b'5f616374696f6e73223a5b2264726166742d646f63756d656e74222c227375626d69'
                            b'742d696d6d757461626c652d646f63756d656e74222c22636f6c6c6563742d726576'
                            b'696577222c22617070726f76652d706c616e6e696e672d696e707574222c22726574'
                            b'75726e2d746f2d617574686f72225d2c22617574686f72697479223a7b2267656e65'
                            b'726174696f6e223a312c226e616d657370616365223a226f72672f616c706861222c'
                            b'22736e617073686f745f646967657374223a22313734386538393336323864633130'
                            b'37666138383464326366343466356136396663643566376436396535373534303432'
                            b'626631396164623065653961643434227d2c2262696e64696e6773223a7b22666f75'
                            b'6e646572223a7b226b696e64223a2268756d616e222c227072696e636970616c223a'
                            b'22666f756e646572222c227465616d223a6e756c6c7d2c22696d706c656d656e7465'
                            b'72223a7b226b696e64223a226167656e74222c227072696e636970616c223a226465'
                            b'765f6167656e74222c227465616d223a22656e67696e656572696e67227d2c227072'
                            b'6f647563742d6c656164223a7b226b696e64223a226167656e74222c227072696e63'
                            b'6970616c223a2270726f647563745f6c656164222c227465616d223a2270726f6475'
                            b'6374227d2c22746573746572223a7b226b696e64223a226167656e74222c22707269'
                            b'6e636970616c223a2271615f656e67696e656572222c227465616d223a22656e6769'
                            b'6e656572696e67227d7d2c22656c696769626c655f7265706c6163656d656e747322'
                            b'3a7b22666f756e646572223a5b5d2c22696d706c656d656e746572223a5b5d2c2270'
                            b'726f647563742d6c656164223a5b5d2c22746573746572223a5b5d7d2c2265787065'
                            b'637465645f61637469766174696f6e5f7265766973696f6e223a302c22696e707574'
                            b'73223a5b5d2c22696e7374616e63655f6964223a2263352d72657461696e65642d73'
                            b'6368656d6131222c226f7065726174696f6e5f6b6579223a2263352d72657461696e'
                            b'65642d736368656d6131222c2273636f7065223a7b226272696566223a2252657461'
                            b'696e656420626f756e64656420736368656d613120646f63756d656e742e227d2c22'
                            b'74656d706c617465223a7b22646566696e6974696f6e5f646967657374223a223064'
                            b'38313534393738393964316536663433303232656338633838353734613336626266'
                            b'32393561303566626630306634313163386138653332346337663537222c22696465'
                            b'6e746974795f6964223a22776f726b666c6f772d74656d706c6174653a3037323732'
                            b'63376663353630313438626434613833616566323366643831303063366663386163'
                            b'34626636346265656630616637373934623261376363396132222c2276657273696f'
                            b'6e223a317d7d","request_digest":"3f5e27e1c845b676e5defbfc64b7cd7fbd2f'
                            b'3cb57527271fe70f761db483238f","task_id":"TASK-900","task_scope_bytes'
                            b'":"7b2261737369676e65645f6167656e74223a2270726f647563745f6c656164222'
                            b'c226272696566223a22576f726b666c6f7720696e697469616c206472616674696e6'
                            b'7207461736b2e2050726f64756365206120626f756e64656420646f63756d656e742'
                            b'06172746966616374206f6e6c792e20446f206e6f742064656c65676174652c20666'
                            b'16e206f75742c20696d706c656d656e7420636f64652c206d657267652c206f72206'
                            b'17070726f76652074686520646f63756d656e742e20436f6d706c6574696f6e20707'
                            b'26573657276657320612064726166743b20696d6d757461626c65207375626d69737'
                            b'3696f6e2f7265766965777320617265206c6174657220756e6974732e5c6e5c6e526'
                            b'57461696e656420626f756e64656420736368656d613120646f63756d656e742e222'
                            b'c227465616d223a2270726f64756374227d","task_scope_digest":"3e1b507762'
                            b'a21bb4e2f85e78abfe41e73f38dba8d356d09a92cca586a710d0e2"},"org_slug":'
                            b'"alpha","previous_digest":"f71d9486ad96c2a4d1222c97af6c0c92c20afae8d'
                            b'67a8f38bff91b40656f5889","result":null,"terminal_evidence":null}',
             'event_digest': '417a218e876efd5bc7d89dac967e31866e75d5a3f7410cb587f2d718c2b92f45',
             'event_kind': 'launch_reserved',
             'event_seq': 3,
             'id': 'f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0:3',
             'intent_id': 'f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0',
             'result_digest': None,
             'result_id': None,
             'session_id': 'sess-c5-historical',
             'state_after': 'claimed',
             'state_before': 'claimed'},
            {'callback_accepted': None,
             'created_at': '2026-10-01T00:00:00+00:00',
             'disposition': None,
             'event_bytes': b'{"after":{"cancellation_requested":0,"claim_owner":"workflow_dispatc'
                            b'her","claim_token":"c5-fixed-claim","final_result_id":null,"host_exe'
                            b'cution_id":"c5-fixed-historical-host","host_launch_started":1,"is_cu'
                            b'rrent":1,"session_id":"sess-c5-historical","state":"running"},"befor'
                            b'e":{"cancellation_requested":0,"claim_owner":"workflow_dispatcher","'
                            b'claim_token":"c5-fixed-claim","final_result_id":null,"host_execution'
                            b'_id":null,"host_launch_started":1,"is_current":1,"session_id":"sess-'
                            b'c5-historical","state":"claimed"},"event":{"created_at":"2026-10-01T'
                            b'00:00:00+00:00","event_kind":"running","event_seq":4,"id":"f019d3c21'
                            b'791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0:4"},"format"'
                            b':"workflow-draft-event@1","intent":{"activation_id":"workflow-activa'
                            b'tion:7dbd124ce2ff79975d018ce40e27bc93fc8814c0fda239fc356da8e21f90a04'
                            b'4","activation_revision":1,"admission_kind":"initial","admission_pri'
                            b'ncipal":"human:founder","assigned_principal":"product_lead","assignm'
                            b'ent_generation":1,"attempt_sequence":1,"authority_digest":"1748e8936'
                            b'28dc107fa884d2cf44f5a69fcd5f7d69e5754042bf19adb0ee9ad44","authority_'
                            b'generation":1,"authority_namespace":"org/alpha/team/product","bindin'
                            b'g_snapshot_id":"workflow-binding:c365db4c52d8530246b4ed14d2e017f633b'
                            b'66b4b7479c84693402a5723b1d91c","context_id":"workflow-context:12e2cd'
                            b'46aad5ead9e871fe2a9e83845d98312d1c4edc5291b045b0c71d42497d","created'
                            b'_at":"2026-10-01T00:00:00+00:00","effect_key":"workflow-initial-draf'
                            b't:workflow-instance:360939a90b15321fac99bc4f5b513bb33da5d0c1e0bfb31d'
                            b'ab5635ec286d2858:1","host_execution_key":"workflow-draft-host:f019d3'
                            b'c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0","id":"f0'
                            b'19d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0","ins'
                            b'tance_id":"workflow-instance:360939a90b15321fac99bc4f5b513bb33da5d0c'
                            b'1e0bfb31dab5635ec286d2858","operation_key":"c5-retained-schema1","pr'
                            b'edecessor_intent_id":null,"recovery_owner":"workflow_recovery","requ'
                            b'est_bytes":"7b22616c6c6f7765645f616374696f6e73223a5b2264726166742d64'
                            b'6f63756d656e74222c227375626d69742d696d6d757461626c652d646f63756d656e'
                            b'74222c22636f6c6c6563742d726576696577222c22617070726f76652d706c616e6e'
                            b'696e672d696e707574222c2272657475726e2d746f2d617574686f72225d2c226175'
                            b'74686f72697479223a7b2267656e65726174696f6e223a312c226e616d6573706163'
                            b'65223a226f72672f616c706861222c22736e617073686f745f646967657374223a22'
                            b'31373438653839333632386463313037666138383464326366343466356136396663'
                            b'643566376436396535373534303432626631396164623065653961643434227d2c22'
                            b'62696e64696e6773223a7b22666f756e646572223a7b226b696e64223a2268756d61'
                            b'6e222c227072696e636970616c223a22666f756e646572222c227465616d223a6e75'
                            b'6c6c7d2c22696d706c656d656e746572223a7b226b696e64223a226167656e74222c'
                            b'227072696e636970616c223a226465765f6167656e74222c227465616d223a22656e'
                            b'67696e656572696e67227d2c2270726f647563742d6c656164223a7b226b696e6422'
                            b'3a226167656e74222c227072696e636970616c223a2270726f647563745f6c656164'
                            b'222c227465616d223a2270726f64756374227d2c22746573746572223a7b226b696e'
                            b'64223a226167656e74222c227072696e636970616c223a2271615f656e67696e6565'
                            b'72222c227465616d223a22656e67696e656572696e67227d7d2c22656c696769626c'
                            b'655f7265706c6163656d656e7473223a7b22666f756e646572223a5b5d2c22696d70'
                            b'6c656d656e746572223a5b5d2c2270726f647563742d6c656164223a5b5d2c227465'
                            b'73746572223a5b5d7d2c2265787065637465645f61637469766174696f6e5f726576'
                            b'6973696f6e223a302c22696e70757473223a5b5d2c22696e7374616e63655f696422'
                            b'3a2263352d72657461696e65642d736368656d6131222c226f7065726174696f6e5f'
                            b'6b6579223a2263352d72657461696e65642d736368656d6131222c2273636f706522'
                            b'3a7b226272696566223a2252657461696e656420626f756e64656420736368656d61'
                            b'3120646f63756d656e742e227d2c2274656d706c617465223a7b22646566696e6974'
                            b'696f6e5f646967657374223a22306438313534393738393964316536663433303232'
                            b'65633863383835373461333662626632393561303566626630306634313163386138'
                            b'653332346337663537222c226964656e746974795f6964223a22776f726b666c6f77'
                            b'2d74656d706c6174653a303732373263376663353630313438626434613833616566'
                            b'32336664383130306336666338616334626636346265656630616637373934623261'
                            b'376363396132222c2276657273696f6e223a317d7d","request_digest":"3f5e27'
                            b'e1c845b676e5defbfc64b7cd7fbd2f3cb57527271fe70f761db483238f","task_id'
                            b'":"TASK-900","task_scope_bytes":"7b2261737369676e65645f6167656e74223'
                            b'a2270726f647563745f6c656164222c226272696566223a22576f726b666c6f77206'
                            b'96e697469616c206472616674696e67207461736b2e2050726f64756365206120626'
                            b'f756e64656420646f63756d656e74206172746966616374206f6e6c792e20446f206'
                            b'e6f742064656c65676174652c2066616e206f75742c20696d706c656d656e7420636'
                            b'f64652c206d657267652c206f7220617070726f76652074686520646f63756d656e7'
                            b'42e20436f6d706c6574696f6e2070726573657276657320612064726166743b20696'
                            b'd6d757461626c65207375626d697373696f6e2f7265766965777320617265206c617'
                            b'4657220756e6974732e5c6e5c6e52657461696e656420626f756e646564207363686'
                            b'56d613120646f63756d656e742e222c227465616d223a2270726f64756374227d","'
                            b'task_scope_digest":"3e1b507762a21bb4e2f85e78abfe41e73f38dba8d356d09a'
                            b'92cca586a710d0e2"},"org_slug":"alpha","previous_digest":"417a218e876'
                            b'efd5bc7d89dac967e31866e75d5a3f7410cb587f2d718c2b92f45","result":null'
                            b',"terminal_evidence":null}',
             'event_digest': '7f4b44b5f239458b490e4708d388a49f335520e6fee92812a79f70c502eca73a',
             'event_kind': 'running',
             'event_seq': 4,
             'id': 'f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0:4',
             'intent_id': 'f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0',
             'result_digest': None,
             'result_id': None,
             'session_id': 'sess-c5-historical',
             'state_after': 'running',
             'state_before': 'claimed'},
            {'callback_accepted': 1,
             'created_at': '2026-10-01T00:00:00+00:00',
             'disposition': 'accepted',
             'event_bytes': b'{"after":{"cancellation_requested":0,"claim_owner":"workflow_dispatc'
                            b'her","claim_token":"c5-fixed-claim","final_result_id":901,"host_exec'
                            b'ution_id":"c5-fixed-historical-host","host_launch_started":1,"is_cur'
                            b'rent":1,"session_id":"sess-c5-historical","state":"running"},"before'
                            b'":{"cancellation_requested":0,"claim_owner":"workflow_dispatcher","c'
                            b'laim_token":"c5-fixed-claim","final_result_id":null,"host_execution_'
                            b'id":"c5-fixed-historical-host","host_launch_started":1,"is_current":'
                            b'1,"session_id":"sess-c5-historical","state":"running"},"event":{"cre'
                            b'ated_at":"2026-10-01T00:00:00+00:00","event_kind":"callback_recorded'
                            b'","event_seq":5,"id":"f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf'
                            b'63c7152b18d38ba5d0:5"},"format":"workflow-draft-event@1","intent":{"'
                            b'activation_id":"workflow-activation:7dbd124ce2ff79975d018ce40e27bc93'
                            b'fc8814c0fda239fc356da8e21f90a044","activation_revision":1,"admission'
                            b'_kind":"initial","admission_principal":"human:founder","assigned_pri'
                            b'ncipal":"product_lead","assignment_generation":1,"attempt_sequence":'
                            b'1,"authority_digest":"1748e893628dc107fa884d2cf44f5a69fcd5f7d69e5754'
                            b'042bf19adb0ee9ad44","authority_generation":1,"authority_namespace":"'
                            b'org/alpha/team/product","binding_snapshot_id":"workflow-binding:c365'
                            b'db4c52d8530246b4ed14d2e017f633b66b4b7479c84693402a5723b1d91c","conte'
                            b'xt_id":"workflow-context:12e2cd46aad5ead9e871fe2a9e83845d98312d1c4ed'
                            b'c5291b045b0c71d42497d","created_at":"2026-10-01T00:00:00+00:00","eff'
                            b'ect_key":"workflow-initial-draft:workflow-instance:360939a90b15321fa'
                            b'c99bc4f5b513bb33da5d0c1e0bfb31dab5635ec286d2858:1","host_execution_k'
                            b'ey":"workflow-draft-host:f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1'
                            b'caf63c7152b18d38ba5d0","id":"f019d3c21791aee69dcfc18e700a7d76fce8c1a'
                            b'92d1caf63c7152b18d38ba5d0","instance_id":"workflow-instance:360939a9'
                            b'0b15321fac99bc4f5b513bb33da5d0c1e0bfb31dab5635ec286d2858","operation'
                            b'_key":"c5-retained-schema1","predecessor_intent_id":null,"recovery_o'
                            b'wner":"workflow_recovery","request_bytes":"7b22616c6c6f7765645f61637'
                            b'4696f6e73223a5b2264726166742d646f63756d656e74222c227375626d69742d696'
                            b'd6d757461626c652d646f63756d656e74222c22636f6c6c6563742d7265766965772'
                            b'22c22617070726f76652d706c616e6e696e672d696e707574222c2272657475726e2'
                            b'd746f2d617574686f72225d2c22617574686f72697479223a7b2267656e657261746'
                            b'96f6e223a312c226e616d657370616365223a226f72672f616c706861222c22736e6'
                            b'17073686f745f646967657374223a223137343865383933363238646331303766613'
                            b'83834643263663434663561363966636435663764363965353735343034326266313'
                            b'96164623065653961643434227d2c2262696e64696e6773223a7b22666f756e64657'
                            b'2223a7b226b696e64223a2268756d616e222c227072696e636970616c223a22666f7'
                            b'56e646572222c227465616d223a6e756c6c7d2c22696d706c656d656e746572223a7'
                            b'b226b696e64223a226167656e74222c227072696e636970616c223a226465765f616'
                            b'7656e74222c227465616d223a22656e67696e656572696e67227d2c2270726f64756'
                            b'3742d6c656164223a7b226b696e64223a226167656e74222c227072696e636970616'
                            b'c223a2270726f647563745f6c656164222c227465616d223a2270726f64756374227'
                            b'd2c22746573746572223a7b226b696e64223a226167656e74222c227072696e63697'
                            b'0616c223a2271615f656e67696e656572222c227465616d223a22656e67696e65657'
                            b'2696e67227d7d2c22656c696769626c655f7265706c6163656d656e7473223a7b226'
                            b'66f756e646572223a5b5d2c22696d706c656d656e746572223a5b5d2c2270726f647'
                            b'563742d6c656164223a5b5d2c22746573746572223a5b5d7d2c22657870656374656'
                            b'45f61637469766174696f6e5f7265766973696f6e223a302c22696e70757473223a5'
                            b'b5d2c22696e7374616e63655f6964223a2263352d72657461696e65642d736368656'
                            b'd6131222c226f7065726174696f6e5f6b6579223a2263352d72657461696e65642d7'
                            b'36368656d6131222c2273636f7065223a7b226272696566223a2252657461696e656'
                            b'420626f756e64656420736368656d613120646f63756d656e742e227d2c2274656d7'
                            b'06c617465223a7b22646566696e6974696f6e5f646967657374223a2230643831353'
                            b'43937383939643165366634333032326563386338383537346133366262663239356'
                            b'1303566626630306634313163386138653332346337663537222c226964656e74697'
                            b'4795f6964223a22776f726b666c6f772d74656d706c6174653a30373237326337666'
                            b'33536303134386264346138336165663233666438313030633666633861633462663'
                            b'6346265656630616637373934623261376363396132222c2276657273696f6e223a3'
                            b'17d7d","request_digest":"3f5e27e1c845b676e5defbfc64b7cd7fbd2f3cb5752'
                            b'7271fe70f761db483238f","task_id":"TASK-900","task_scope_bytes":"7b22'
                            b'61737369676e65645f6167656e74223a2270726f647563745f6c656164222c226272'
                            b'696566223a22576f726b666c6f7720696e697469616c206472616674696e67207461'
                            b'736b2e2050726f64756365206120626f756e64656420646f63756d656e7420617274'
                            b'6966616374206f6e6c792e20446f206e6f742064656c65676174652c2066616e206f'
                            b'75742c20696d706c656d656e7420636f64652c206d657267652c206f722061707072'
                            b'6f76652074686520646f63756d656e742e20436f6d706c6574696f6e207072657365'
                            b'7276657320612064726166743b20696d6d757461626c65207375626d697373696f6e'
                            b'2f7265766965777320617265206c6174657220756e6974732e5c6e5c6e5265746169'
                            b'6e656420626f756e64656420736368656d613120646f63756d656e742e222c227465'
                            b'616d223a2270726f64756374227d","task_scope_digest":"3e1b507762a21bb4e'
                            b'2f85e78abfe41e73f38dba8d356d09a92cca586a710d0e2"},"org_slug":"alpha"'
                            b',"previous_digest":"7f4b44b5f239458b490e4708d388a49f335520e6fee92812'
                            b'a79f70c502eca73a","result":{"accepted":1,"digest":"56163ab1099632080'
                            b'f3ee9586bb84fe5e26e97279d6517bda225ec20a58ecb6f","disposition":"acce'
                            b'pted","id":901,"record":{"agent":"product_lead","confidence_score":8'
                            b'5,"created_at":"2026-10-01T00:00:00+00:00","decision_json":null,"dur'
                            b'ation_seconds":1,"estimated_cost":null,"id":901,"learnings":null,"lo'
                            b'cal_ci":null,"output_dir":null,"output_summary":"Retained completed '
                            b'schema1 fixture.","risks_flagged":null,"session_id":"sess-c5-histori'
                            b'cal","status":"completed","task_id":"TASK-900","token_count":null,"v'
                            b'erdict":null,"waiting_on_job_ids":null}},"terminal_evidence":null}',
             'event_digest': '732cdfaa42949e8b4352946b1619da3310d3dee894e47c8874fb0555aa69cb32',
             'event_kind': 'callback_recorded',
             'event_seq': 5,
             'id': 'f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0:5',
             'intent_id': 'f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0',
             'result_digest': '56163ab1099632080f3ee9586bb84fe5e26e97279d6517bda225ec20a58ecb6f',
             'result_id': 901,
             'session_id': 'sess-c5-historical',
             'state_after': 'running',
             'state_before': 'running'},
            {'callback_accepted': None,
             'created_at': '2026-10-01T00:00:00+00:00',
             'disposition': None,
             'event_bytes': b'{"after":{"cancellation_requested":0,"claim_owner":"workflow_dispatc'
                            b'her","claim_token":"c5-fixed-claim","final_result_id":901,"host_exec'
                            b'ution_id":"c5-fixed-historical-host","host_launch_started":1,"is_cur'
                            b'rent":1,"session_id":"sess-c5-historical","state":"completed"},"befo'
                            b're":{"cancellation_requested":0,"claim_owner":"workflow_dispatcher",'
                            b'"claim_token":"c5-fixed-claim","final_result_id":901,"host_execution'
                            b'_id":"c5-fixed-historical-host","host_launch_started":1,"is_current"'
                            b':1,"session_id":"sess-c5-historical","state":"running"},"event":{"cr'
                            b'eated_at":"2026-10-01T00:00:00+00:00","event_kind":"completed","even'
                            b't_seq":6,"id":"f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152'
                            b'b18d38ba5d0:6"},"format":"workflow-draft-event@1","intent":{"activat'
                            b'ion_id":"workflow-activation:7dbd124ce2ff79975d018ce40e27bc93fc8814c'
                            b'0fda239fc356da8e21f90a044","activation_revision":1,"admission_kind":'
                            b'"initial","admission_principal":"human:founder","assigned_principal"'
                            b':"product_lead","assignment_generation":1,"attempt_sequence":1,"auth'
                            b'ority_digest":"1748e893628dc107fa884d2cf44f5a69fcd5f7d69e5754042bf19'
                            b'adb0ee9ad44","authority_generation":1,"authority_namespace":"org/alp'
                            b'ha/team/product","binding_snapshot_id":"workflow-binding:c365db4c52d'
                            b'8530246b4ed14d2e017f633b66b4b7479c84693402a5723b1d91c","context_id":'
                            b'"workflow-context:12e2cd46aad5ead9e871fe2a9e83845d98312d1c4edc5291b0'
                            b'45b0c71d42497d","created_at":"2026-10-01T00:00:00+00:00","effect_key'
                            b'":"workflow-initial-draft:workflow-instance:360939a90b15321fac99bc4f'
                            b'5b513bb33da5d0c1e0bfb31dab5635ec286d2858:1","host_execution_key":"wo'
                            b'rkflow-draft-host:f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7'
                            b'152b18d38ba5d0","id":"f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf'
                            b'63c7152b18d38ba5d0","instance_id":"workflow-instance:360939a90b15321'
                            b'fac99bc4f5b513bb33da5d0c1e0bfb31dab5635ec286d2858","operation_key":"'
                            b'c5-retained-schema1","predecessor_intent_id":null,"recovery_owner":"'
                            b'workflow_recovery","request_bytes":"7b22616c6c6f7765645f616374696f6e'
                            b'73223a5b2264726166742d646f63756d656e74222c227375626d69742d696d6d7574'
                            b'61626c652d646f63756d656e74222c22636f6c6c6563742d726576696577222c2261'
                            b'7070726f76652d706c616e6e696e672d696e707574222c2272657475726e2d746f2d'
                            b'617574686f72225d2c22617574686f72697479223a7b2267656e65726174696f6e22'
                            b'3a312c226e616d657370616365223a226f72672f616c706861222c22736e61707368'
                            b'6f745f646967657374223a2231373438653839333632386463313037666138383464'
                            b'32636634346635613639666364356637643639653537353430343262663139616462'
                            b'3065653961643434227d2c2262696e64696e6773223a7b22666f756e646572223a7b'
                            b'226b696e64223a2268756d616e222c227072696e636970616c223a22666f756e6465'
                            b'72222c227465616d223a6e756c6c7d2c22696d706c656d656e746572223a7b226b69'
                            b'6e64223a226167656e74222c227072696e636970616c223a226465765f6167656e74'
                            b'222c227465616d223a22656e67696e656572696e67227d2c2270726f647563742d6c'
                            b'656164223a7b226b696e64223a226167656e74222c227072696e636970616c223a22'
                            b'70726f647563745f6c656164222c227465616d223a2270726f64756374227d2c2274'
                            b'6573746572223a7b226b696e64223a226167656e74222c227072696e636970616c22'
                            b'3a2271615f656e67696e656572222c227465616d223a22656e67696e656572696e67'
                            b'227d7d2c22656c696769626c655f7265706c6163656d656e7473223a7b22666f756e'
                            b'646572223a5b5d2c22696d706c656d656e746572223a5b5d2c2270726f647563742d'
                            b'6c656164223a5b5d2c22746573746572223a5b5d7d2c2265787065637465645f6163'
                            b'7469766174696f6e5f7265766973696f6e223a302c22696e70757473223a5b5d2c22'
                            b'696e7374616e63655f6964223a2263352d72657461696e65642d736368656d613122'
                            b'2c226f7065726174696f6e5f6b6579223a2263352d72657461696e65642d73636865'
                            b'6d6131222c2273636f7065223a7b226272696566223a2252657461696e656420626f'
                            b'756e64656420736368656d613120646f63756d656e742e227d2c2274656d706c6174'
                            b'65223a7b22646566696e6974696f6e5f646967657374223a22306438313534393738'
                            b'39396431653666343330323265633863383835373461333662626632393561303566'
                            b'626630306634313163386138653332346337663537222c226964656e746974795f69'
                            b'64223a22776f726b666c6f772d74656d706c6174653a303732373263376663353630'
                            b'31343862643461383361656632336664383130306336666338616334626636346265'
                            b'656630616637373934623261376363396132222c2276657273696f6e223a317d7d",'
                            b'"request_digest":"3f5e27e1c845b676e5defbfc64b7cd7fbd2f3cb57527271fe7'
                            b'0f761db483238f","task_id":"TASK-900","task_scope_bytes":"7b226173736'
                            b'9676e65645f6167656e74223a2270726f647563745f6c656164222c2262726965662'
                            b'23a22576f726b666c6f7720696e697469616c206472616674696e67207461736b2e2'
                            b'050726f64756365206120626f756e64656420646f63756d656e74206172746966616'
                            b'374206f6e6c792e20446f206e6f742064656c65676174652c2066616e206f75742c2'
                            b'0696d706c656d656e7420636f64652c206d657267652c206f7220617070726f76652'
                            b'074686520646f63756d656e742e20436f6d706c6574696f6e2070726573657276657'
                            b'320612064726166743b20696d6d757461626c65207375626d697373696f6e2f72657'
                            b'66965777320617265206c6174657220756e6974732e5c6e5c6e52657461696e65642'
                            b'0626f756e64656420736368656d613120646f63756d656e742e222c227465616d223'
                            b'a2270726f64756374227d","task_scope_digest":"3e1b507762a21bb4e2f85e78'
                            b'abfe41e73f38dba8d356d09a92cca586a710d0e2"},"org_slug":"alpha","previ'
                            b'ous_digest":"732cdfaa42949e8b4352946b1619da3310d3dee894e47c8874fb055'
                            b'5aa69cb32","result":{"accepted":1,"digest":"56163ab1099632080f3ee958'
                            b'6bb84fe5e26e97279d6517bda225ec20a58ecb6f","disposition":"accepted","'
                            b'id":901,"record":{"agent":"product_lead","confidence_score":85,"crea'
                            b'ted_at":"2026-10-01T00:00:00+00:00","decision_json":null,"duration_s'
                            b'econds":1,"estimated_cost":null,"id":901,"learnings":null,"local_ci"'
                            b':null,"output_dir":null,"output_summary":"Retained completed schema1'
                            b' fixture.","risks_flagged":null,"session_id":"sess-c5-historical","s'
                            b'tatus":"completed","task_id":"TASK-900","token_count":null,"verdict"'
                            b':null,"waiting_on_job_ids":null}},"terminal_evidence":{"host_quiesce'
                            b'nt":true}}',
             'event_digest': 'ef6afdbd4b3f508e2425e51d201b6325ba5ee6d203bcc23593d965c719afb022',
             'event_kind': 'completed',
             'event_seq': 6,
             'id': 'f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0:6',
             'intent_id': 'f019d3c21791aee69dcfc18e700a7d76fce8c1a92d1caf63c7152b18d38ba5d0',
             'result_digest': None,
             'result_id': None,
             'session_id': 'sess-c5-historical',
             'state_after': 'completed',
             'state_before': 'running'}],
 'projection': {'cancellation_requested': 0,
                'claim_owner': 'workflow_dispatcher',
                'claim_token': 'c5-fixed-claim',
                'final_result_id': 901,
                'host_execution_id': 'c5-fixed-historical-host',
                'host_launch_started': 1,
                'is_current': 1,
                'session_id': 'sess-c5-historical',
                'state': 'completed'},
 'result': {'agent': 'product_lead',
            'confidence_score': 85,
            'created_at': '2026-10-01T00:00:00+00:00',
            'decision_json': None,
            'duration_seconds': 1,
            'estimated_cost': None,
            'id': 901,
            'learnings': None,
            'local_ci': None,
            'output_dir': None,
            'output_summary': 'Retained completed schema1 fixture.',
            'risks_flagged': None,
            'session_id': 'sess-c5-historical',
            'status': 'completed',
            'task_id': 'TASK-900',
            'token_count': None,
            'verdict': None,
            'waiting_on_job_ids': None},
 'result_digest': '56163ab1099632080f3ee9586bb84fe5e26e97279d6517bda225ec20a58ecb6f'}
