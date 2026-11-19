# DebugLogManager install templates

The CloudFormation templates DebugLogManager installs into your AWS accounts,
published so you can read exactly what gets created before installing.
DebugLogManager itself is sold on AWS Marketplace; these templates are its
footprint in your accounts.

## What's here

| Template | Where it goes | What it creates |
|---|---|---|
| [`templates/install.yaml`](templates/install.yaml) | the tools account (from AWS Marketplace) | The VM (no inbound ports, no SSH key, IMDSv2 only) and its role `DebugLogManager-vm`; the audit bucket (Object Lock, Governance, 1 year); and the two StackSets below. The Update button updates this stack as the VM role, which can only replace its own VM. Generated from `templates/src/install.src.yaml` by `tools/build_install.py`. |
| [`templates/reader.yaml`](templates/reader.yaml) | every account in scope (StackSet) | `DebugLogManager-reader`: read-only. Lists resources and their current logging settings. |
| [`templates/worker.yaml`](templates/worker.yaml) | every account in scope, one region (StackSet) | `DebugLogManager-worker`: turns logging on and off, only for the sources and regions you allow. Inert at install. |
| [`templates/management.yaml`](templates/management.yaml) | the account running IAM Identity Center | `DebugLogManager-identity-reader`: four read actions, to list a user's account assignments at sign-in. |

Every role's permissions are also in [`policies/`](policies/) as plain JSON
(generated from these templates by `tools/export_policies.py`), with the
policy each person needs ([`policies/user-policy.json`](policies/user-policy.json):
find the VM and open a port-forwarding tunnel to it, never a shell).

## Install

1. **Management account, once:** let the tools account manage StackSets for
   the organization (skip what's already done):
   ```
   aws organizations enable-aws-service-access --service-principal member.org.stacksets.cloudformation.amazonaws.com
   aws organizations register-delegated-administrator --service-principal member.org.stacksets.cloudformation.amazonaws.com --account-id <tools-account-id>
   ```
2. **Tools account:** subscribe on AWS Marketplace and launch the install
   stack. Choose a subnet with outbound internet, the OUs DebugLogManager may
   debug in (or the root for the whole organization), accounts to leave out,
   the regions to offer and the Identity Center account and region.
3. **Identity Center account:** launch `management.yaml` with the tools
   account id and the install stack's `ExternalId` output.
4. **Connect:** open the SSM tunnel to the VM (tag `Name=debuglogmanager`) and
   sign in with `aws eks get-token --cluster-name debuglogmanager`.

Uninstalling: delete the install stack (it removes the StackSets and their
roles in every account) and the management stack. The audit bucket is kept,
because its entries are locked; empty and delete it when you no longer need
it.

## How the roles are protected

- Both roles trust only DebugLogManager's own role in your tools account, and
  only with your install's ExternalId.
- Every assume carries a source identity: the name of the person who asked
  (or `debuglogmanager` for its own background work), so each account's
  CloudTrail shows who did what.
- The worker role is **inert** until an admin allows something: no
  permissions, and its trust policy denies DebugLogManager.
- Each worker parameter is one permission: a source's region list (`none` =
  not allowed), the global sources, and two switches for log group retention
  and delete. Each regional permission is limited with `aws:RequestedRegion`.
- What DebugLogManager creates is named `dlm-…` (buckets, trails) or
  `/debuglogmanager/…` (log groups) and tagged `dlm:managed=true`; where AWS
  allows it, the worker can only change or delete those.
- Some settings are plain resource attributes that IAM can't limit to what
  DebugLogManager created (ALB access log attributes, S3 bucket logging,
  Lambda logging configuration). There, DebugLogManager's own check is what
  stops it touching logging it didn't turn on: it refuses any resource that
  already logs somewhere else, and puts back exactly what it found.
- Control Tower's log groups are never changed (an explicit deny).

## Sources

All twenty: VPC and Transit Gateway flow logs, Network Firewall, Route 53
Resolver query logs, ALB and NLB access logs, CloudFront, WAF (regional and
CloudFront), API Gateway access and execution logs, Route 53 public zone
query logs, CloudTrail data events (to S3), S3 server access logs, Lambda
debug level, RDS log exports (instances), EKS control plane, Step Functions,
ElastiCache and OpenSearch.

## What else DebugLogManager may leave in an account

- `DebugLogManager-log-delivery`, a CloudWatch Logs resource policy (one per
  region) that lets AWS services write to DebugLogManager's own log groups.
- API Gateway's account-level CloudWatch role, set to
  `DebugLogManager-apigateway-cloudwatch` **only if the account had none**.
  It's never replaced, and stays after logging is turned off (other APIs may
  start using it).
- Route 53 Resolver's service-linked role, which AWS creates the first time a
  query log config is made.
- Step Functions: AWS checks the state machine's own role for the log
  delivery permissions; if it lacks them, turning logging on fails with a
  message saying so.

## If your SCPs protect CloudTrail

A common guardrail SCP denies `cloudtrail:PutEventSelectors`, `StopLogging`,
`DeleteTrail` and `UpdateTrail` on every trail. CloudTrail data events need
those on DebugLogManager's own trails (named `dlm-…`). Either exempt
`arn:aws:iam::*:role/DebugLogManager-*` from that statement, limit the
statement to your own trails, or don't allow the CloudTrail data events
source.

## Recommended: protect the roles with an SCP

[`policies/example-scp-protect-roles.json`](policies/example-scp-protect-roles.json)
denies changing the reader and worker roles (and the worker's policies) to
everyone except the StackSets execution role (`stacksets-exec-*`), so an
inert worker can only be opened the way DebugLogManager shows. Attach it to
the OUs in scope. It leaves `DebugLogManager-vm` alone, so the tools
account's admins can still install, update and uninstall.

Tested: with it attached, an administrator's direct edits to the worker role
and its policies are denied, and allowing a source through the worker
StackSet still works.
