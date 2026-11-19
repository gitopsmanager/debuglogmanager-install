# DebugLogManager permissions, as plain JSON

For reviewing what DebugLogManager can do without reading CloudFormation.
Values in angle brackets differ per install (`<tools-account-id>`,
`<external-id>`, `<allowed regions: ...>`).

| File | What it is |
|---|---|
| `DebugLogManager-vm.json` | The VM's role, in the tools account. |
| `DebugLogManager-reader.json` | The read-only role in every account in scope. |
| `DebugLogManager-worker.json` | The role that turns logging on and off in every account in scope, shown with **every source allowed**: the most it can ever do. At install nothing is allowed and its trust is closed. |
| `DebugLogManager-flowlogs-delivery.json`, `DebugLogManager-apigateway-cloudwatch.json` | Service roles the worker hands to VPC/Transit Gateway flow logs and API Gateway. |
| `DebugLogManager-identity-reader.json` | Identity Center read, in the management account. |
| `user-policy.json` | What a person needs to use DebugLogManager: find the VM, open a port-forwarding tunnel to it (never a shell), end their own sessions. Add it to their permission set. |
| `example-scp-protect-roles.json` | Optional. Lets only StackSets change the reader and worker roles and the worker's policies, so nobody with IAM rights in a member account can widen them. Attach it to the OUs in scope. It leaves the VM role alone, so the tools account's admins can still install, update and uninstall. |

The role files are generated from the templates by `tools/export_policies.py`
and never edited by hand; `user-policy.json` and the example SCP are written
by hand.
