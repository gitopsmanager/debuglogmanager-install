"""Writes every IAM policy the templates create as plain JSON, under policies/,
for people who review permissions without reading CloudFormation.

    python tools/export_policies.py

Each role gets one file: its trust policy and every policy attached to it
(inline and DebugLogManager managed policies). The worker is shown with
EVERY source allowed in every region it's allowed in, so the file is the most
it can ever do; at install nothing is allowed and its trust is closed. Values
that differ per install are placeholders in angle brackets, such as
<tools-account-id> or <allowed regions: VpcFlowLogsRegions>.

The files are generated, never edited by hand, so they can't drift from the
templates that were tested.
"""
import json
import pathlib
import re

import yaml

root = pathlib.Path(__file__).resolve().parent.parent
out_dir = root / "policies"


class Tag:
    """A CloudFormation short-form intrinsic, kept as {"Fn::X": value}."""


def _construct(loader, suffix, node):
    name = "Ref" if suffix == "Ref" else "Fn::GetAtt" if suffix == "GetAtt" else \
        "Condition" if suffix == "Condition" else f"Fn::{suffix}"
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
        if suffix == "GetAtt":
            value = value.split(".", 1)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    return {name: value}


class Loader(yaml.SafeLoader):
    pass


Loader.add_multi_constructor("!", _construct)


def load(path):
    return yaml.load(path.read_text(encoding="utf-8"), Loader=Loader)


class Render:
    def __init__(self, template, params, account):
        self.t = template
        self.params = params
        self.pseudo = {"AWS::Partition": "aws", "AWS::AccountId": account, "AWS::Region": "<region>",
                       "AWS::StackName": "<stack-name>", "AWS::StackId": "<this-stack-id>",
                       "AWS::URLSuffix": "amazonaws.com"}
        self.cond_cache = {}

    def ref(self, name):
        if name in self.pseudo:
            return self.pseudo[name]
        if name in self.params:
            return self.params[name]
        res = self.t.get("Resources", {}).get(name)
        if res and res["Type"] == "AWS::IAM::Role":
            return res["Properties"].get("RoleName", f"<{name}>")
        return f"<{name}>"

    def getatt(self, res, attr):
        r = self.t.get("Resources", {}).get(res, {})
        if r.get("Type") == "AWS::IAM::Role" and attr == "Arn":
            return f"arn:aws:iam::{self.pseudo['AWS::AccountId']}:role/{r['Properties'].get('RoleName', res)}"
        if r.get("Type") == "AWS::S3::Bucket" and attr == "Arn":
            return "arn:aws:s3:::<state-bucket>"
        return f"<{res}.{attr}>"

    def cond(self, name):
        if name not in self.cond_cache:
            self.cond_cache[name] = bool(self.ev(self.t["Conditions"][name]))
        return self.cond_cache[name]

    def ev(self, v):
        if isinstance(v, list):
            out = [self.ev(x) for x in v]
            return [x for x in out if x is not NOVALUE]
        if not isinstance(v, dict):
            return v
        if len(v) == 1:
            (k, a), = v.items()
            if k == "Ref":
                return NOVALUE if a == "AWS::NoValue" else self.ref(a)
            if k == "Condition" and isinstance(a, str):
                return self.cond(a)
            if k == "Fn::GetAtt":
                return self.getatt(*a)
            if k == "Fn::Sub":
                text, extra = (a, {}) if isinstance(a, str) else (a[0], {kk: self.ev(vv) for kk, vv in a[1].items()})

                def sub(m):
                    n = m.group(1)
                    if n in extra:
                        return str(extra[n])
                    if "." in n:
                        return self.getatt(*n.split(".", 1))
                    return str(self.ref(n))
                return re.sub(r"\$\{([^}!]+)\}", sub, text)
            if k == "Fn::Join":
                return a[0].join(str(x) for x in self.ev(a[1]))
            if k == "Fn::Split":
                return str(self.ev(a[1])).split(a[0])
            if k == "Fn::Select":
                return self.ev(a[1])[int(self.ev(a[0]))]
            if k == "Fn::If":
                return self.ev(a[1] if self.cond(a[0]) else a[2])
            if k == "Fn::Equals":
                return self.ev(a[0]) == self.ev(a[1])
            if k == "Fn::Not":
                return not self.ev(a[0])
            if k == "Fn::Or":
                return any(self.ev(x) for x in a)
            if k == "Fn::And":
                return all(self.ev(x) for x in a)
        out = {}
        for k, x in v.items():
            y = self.ev(x)
            if y is not NOVALUE:
                out[k] = y
        return out


NOVALUE = object()


def roles(template, params, account):
    r = Render(template, params, account)
    res = template.get("Resources", {})
    found = {}
    for name, d in res.items():
        if d["Type"] != "AWS::IAM::Role" or ("Condition" in d and not r.cond(d["Condition"])):
            continue
        p = d["Properties"]
        role = p.get("RoleName", name)
        doc = {"Role": role, "TrustPolicy": r.ev(p["AssumeRolePolicyDocument"]),
               "AwsManagedPolicies": [r.ev(m) for m in p.get("ManagedPolicyArns", [])], "Policies": {}}
        for pol in p.get("Policies", []):
            doc["Policies"][pol["PolicyName"]] = r.ev(pol["PolicyDocument"])
        found[name] = doc
    for name, d in res.items():
        if d["Type"] != "AWS::IAM::ManagedPolicy" or ("Condition" in d and not r.cond(d["Condition"])):
            continue
        p = d["Properties"]
        for target in p.get("Roles", []):
            key = target["Ref"] if isinstance(target, dict) else target
            if key in found:
                found[key]["Policies"][p.get("ManagedPolicyName", name)] = r.ev(p["PolicyDocument"])
    return list(found.values())


def everything_allowed(template):
    """Parameter values with every source allowed (worker) and placeholders."""
    params = {}
    for name, d in template.get("Parameters", {}).items():
        if name.endswith("Regions"):
            params[name] = f"<allowed regions: {name}>"
        elif name == "AllowedGlobalSources":
            params[name] = "cloudfront-logs,route53-query-logs,waf-cloudfront-logs"
        elif d.get("AllowedValues") == ["false", "true"]:
            params[name] = "true"
        elif name == "ToolsAccountId":
            params[name] = "<tools-account-id>"
        elif name == "AppPrincipalArn":
            params[name] = "arn:aws:iam::<tools-account-id>:role/DebugLogManager-vm"
        elif name == "ExternalId":
            params[name] = "<external-id>"
        elif name == "IdentityCenterAccountId":
            params[name] = "<management-account-id>"
        else:
            params[name] = f"<{name}>"
    return params


def main():
    out_dir.mkdir(exist_ok=True)
    sources = [("install", root / "templates" / "src" / "install.src.yaml", "<tools-account-id>"),
               ("reader", root / "templates" / "reader.yaml", "<account-id>"),
               ("worker", root / "templates" / "worker.yaml", "<account-id>"),
               ("management", root / "templates" / "management.yaml", "<management-account-id>")]
    for label, path, account in sources:
        t = load(path)
        for doc in roles(t, everything_allowed(t), account):
            f = out_dir / f"{doc['Role']}.json"
            f.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
            print(f"policies/{f.name}  ({label}.yaml)")


if __name__ == "__main__":
    main()
