"""Real-world scenario tests for trimerge.

Each class models a concrete ecosystem where the three-way merge problem
appears, drawn from the internet research that identified trimerge's gap:

- Kubernetes / Helm  — values.yaml chart upgrades vs user overrides
- dbt               — schema.yml auto-generated descriptions vs user annotations
- Project scaffolding (copier/cookiecutter) — template updates vs user edits
- Ansible           — generated inventory/group_vars vs operator overrides
- OpenAPI / Swagger — generated spec vs developer extensions
- GitHub Actions    — workflow templates vs repository customisations
"""

from __future__ import annotations

import copy

import pytest

from trimerge import DELETED, Conflict, merge


def conflict_paths(conflicts: list[Conflict]) -> list[str]:
    return [c.path for c in conflicts]


# ── Kubernetes / Helm ────────────────────────────────────────────────────────
#
# Problem (from helm/helm#11520): helm upgrade re-renders values.yaml from
# the chart; user overrides in the deployed ConfigMap get stomped.
# trimerge lets the operator keep their overrides while taking the chart's
# new defaults where they haven't touched anything.


class TestHelm:
    """Simulate upgrading a Helm chart while preserving operator overrides."""

    BASE = {
        "replicaCount": 1,
        "image": {
            "repository": "nginx",
            "tag": "1.24",
            "pullPolicy": "IfNotPresent",
        },
        "resources": {
            "limits":   {"cpu": "100m", "memory": "128Mi"},
            "requests": {"cpu": "50m",  "memory": "64Mi"},
        },
        "service": {"type": "ClusterIP", "port": 80},
        "ingress": {"enabled": False},
        "autoscaling": {"enabled": False, "minReplicas": 1, "maxReplicas": 10},
    }

    def test_chart_upgrade_preserves_operator_replica_override(self):
        """Operator scaled to 3 replicas; new chart keeps 1 as default."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        ours["replicaCount"] = 3            # operator changed
        theirs["image"]["tag"] = "1.26"     # chart bumped image tag

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert merged["replicaCount"] == 3      # operator override preserved
        assert merged["image"]["tag"] == "1.26" # chart update applied
        assert conflicts == []

    def test_new_chart_adds_resource_key_operator_never_set(self):
        """Chart v2 adds a new top-level key the operator never knew about."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)
        theirs["podAnnotations"] = {"prometheus.io/scrape": "true"}

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert "podAnnotations" in merged
        assert merged["podAnnotations"]["prometheus.io/scrape"] == "true"
        assert conflicts == []

    def test_operator_enables_ingress_chart_also_changes_ingress(self):
        """Both operator and chart changed ingress — conflict on hostname."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        ours["ingress"]   = {"enabled": True, "hostname": "my.company.com"}
        theirs["ingress"] = {"enabled": True, "hostname": "default.example.com"}

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert len(conflicts) == 1
        assert "hostname" in conflicts[0].path
        assert merged["ingress"]["enabled"] is True  # non-conflicting key merged

    def test_operator_bumped_memory_limit_chart_bumped_cpu_limit(self):
        """Two orthogonal resource changes — both should land without conflict."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        ours["resources"]["limits"]["memory"]   = "256Mi"  # operator
        theirs["resources"]["limits"]["cpu"]    = "200m"   # chart

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert merged["resources"]["limits"]["memory"] == "256Mi"
        assert merged["resources"]["limits"]["cpu"]    == "200m"
        assert conflicts == []

    def test_operator_removed_autoscaling_key_chart_updated_it(self):
        """Operator deleted autoscaling block; chart also touched maxReplicas."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        del ours["autoscaling"]                           # operator removed block
        theirs["autoscaling"]["maxReplicas"] = 20         # chart updated max

        merged, conflicts = merge(self.BASE, ours, theirs)
        # User deleted, chart changed → conflict; deletion wins (ours)
        assert "autoscaling" not in merged
        assert len(conflicts) == 1
        assert conflicts[0].ours_val is DELETED


# ── dbt schema.yml ───────────────────────────────────────────────────────────
#
# Problem (from mikefarah/yq discussion #1223): dbt auto-generates
# schema.yml from introspection.  Engineers annotate descriptions and add
# tests.  Next dbt run adds new columns; existing annotations must survive.


class TestDbt:
    """Simulate dbt schema.yml collect + user annotation lifecycle."""

    BASE = {
        "version": 2,
        "models": [
            {
                "name": "orders",
                "description": "",
                "columns": [
                    {"name": "order_id",    "description": "", "tests": []},
                    {"name": "customer_id", "description": "", "tests": []},
                    {"name": "status",      "description": "", "tests": []},
                ],
            }
        ],
    }

    def test_engineer_adds_descriptions_new_run_adds_column(self):
        """Engineer annotated columns; new dbt run found a new column."""
        ours = copy.deepcopy(self.BASE)
        ours["models"][0]["description"] = "Core orders model"
        ours["models"][0]["columns"][0]["description"] = "Surrogate primary key"
        ours["models"][0]["columns"][1]["description"] = "FK to customers"
        ours["models"][0]["columns"][2]["tests"] = ["accepted_values"]

        theirs = copy.deepcopy(self.BASE)
        theirs["models"][0]["columns"].append(
            {"name": "total_amount", "description": "", "tests": []}
        )

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert conflicts == []

        model = merged["models"][0]
        assert model["description"] == "Core orders model"
        col_names = [c["name"] for c in model["columns"]]
        assert "total_amount" in col_names  # new column added by dbt

        cols = {c["name"]: c for c in model["columns"]}
        assert cols["order_id"]["description"] == "Surrogate primary key"  # preserved
        assert cols["status"]["tests"] == ["accepted_values"]              # preserved

    def test_dbt_removes_dropped_column_engineer_had_annotated_it(self):
        """Source table dropped a column; engineer had added a test for it."""
        ours = copy.deepcopy(self.BASE)
        ours["models"][0]["columns"][2]["tests"] = ["not_null"]  # engineer added test

        theirs = copy.deepcopy(self.BASE)
        theirs["models"][0]["columns"] = [
            c for c in theirs["models"][0]["columns"]
            if c["name"] != "status"   # status column dropped from source
        ]

        merged, conflicts = merge(self.BASE, ours, theirs)
        # Engineer added test on status; dbt removed status → conflict
        assert len(conflicts) == 1
        col_names = [c["name"] for c in merged["models"][0]["columns"]]
        # ours wins on conflict — status kept with engineer's test
        assert "status" in col_names

    def test_two_engineers_annotate_different_models_no_conflict(self):
        """Two engineers each annotated a different model — CI merge is clean."""
        base = {
            "version": 2,
            "models": [
                {"name": "orders",    "description": "", "columns": []},
                {"name": "customers", "description": "", "columns": []},
            ],
        }
        ours = copy.deepcopy(base)
        ours["models"][0]["description"] = "Engineer A wrote this"

        theirs = copy.deepcopy(base)
        theirs["models"][1]["description"] = "Engineer B wrote this"

        merged, conflicts = merge(base, ours, theirs)
        assert conflicts == []
        models = {m["name"]: m for m in merged["models"]}
        assert models["orders"]["description"]    == "Engineer A wrote this"
        assert models["customers"]["description"] == "Engineer B wrote this"

    def test_dbt_adds_new_model_no_conflict(self):
        """New dbt introspection discovers a new model; user file untouched."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)
        theirs["models"].append(
            {"name": "returns", "description": "", "columns": []}
        )

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert conflicts == []
        names = [m["name"] for m in merged["models"]]
        assert "returns" in names


# ── Project scaffolding (copier / cookiecutter) ───────────────────────────────
#
# Problem (from copier PR #407): when the project template is updated,
# copier needs to apply template changes to the already-generated project
# without overwriting developer customisations.


class TestCopier:
    """Simulate re-applying an updated project template to a customised project."""

    BASE = {
        "project_name": "my_service",
        "python_version": "3.11",
        "ci": {
            "provider": "github_actions",
            "python_versions": ["3.10", "3.11"],
            "lint": True,
            "test": True,
            "deploy": False,
        },
        "docker": {
            "enabled": False,
            "base_image": "python:3.11-slim",
        },
        "dependencies": ["requests", "pydantic"],
    }

    def test_template_adds_ci_feature_developer_enabled_docker(self):
        """Template adds `security_scan`; developer enabled Docker."""
        ours = copy.deepcopy(self.BASE)
        ours["docker"] = {"enabled": True, "base_image": "python:3.11-slim", "registry": "ghcr.io"}

        theirs = copy.deepcopy(self.BASE)
        theirs["ci"]["security_scan"] = True        # new template feature
        theirs["ci"]["python_versions"] = ["3.10", "3.11", "3.12"]  # template updated list

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert conflicts == []
        assert merged["docker"]["enabled"] is True         # developer's change preserved
        assert merged["docker"]["registry"] == "ghcr.io"  # developer's change preserved
        assert merged["ci"]["security_scan"] is True       # template feature added
        assert "3.12" in merged["ci"]["python_versions"]   # template list update applied

    def test_developer_added_dependency_template_also_added_different_one(self):
        """Developer and template both added a new dependency (different one)."""
        ours = copy.deepcopy(self.BASE)
        ours["dependencies"] = [*self.BASE["dependencies"], "httpx"]  # developer added

        theirs = copy.deepcopy(self.BASE)
        theirs["dependencies"] = [*self.BASE["dependencies"], "structlog"]  # template added

        # Dependencies is a plain list (no 'name' key) and all three differ → conflict
        merged, conflicts = merge(self.BASE, ours, theirs)
        assert len(conflicts) == 1
        assert "dependencies" in conflicts[0].path

    def test_template_renames_key_developer_untouched(self):
        """Template renamed `lint` to `linting`; developer never touched it."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        del theirs["ci"]["lint"]
        theirs["ci"]["linting"] = {"enabled": True, "tool": "ruff"}

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert conflicts == []
        assert "lint" not in merged["ci"]
        assert merged["ci"]["linting"]["tool"] == "ruff"

    def test_developer_changed_python_version_template_also_changed_it(self):
        """Both developer and template updated python_version — conflict."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        ours["python_version"]   = "3.12"   # developer upgraded
        theirs["python_version"] = "3.13"   # template recommends latest

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert len(conflicts) == 1
        assert conflicts[0].ours_val   == "3.12"
        assert conflicts[0].theirs_val == "3.13"
        assert merged["python_version"] == "3.12"  # ours wins


# ── Ansible group_vars ────────────────────────────────────────────────────────
#
# Ansible playbooks auto-generate group_vars files from inventory discovery.
# Operators override settings for specific environments without touching
# unrelated variables.


class TestAnsible:
    """Simulate Ansible group_vars merge between playbook defaults and operator overrides."""

    BASE = {
        "ntp_servers":  ["0.pool.ntp.org", "1.pool.ntp.org"],
        "dns_servers":  ["8.8.8.8", "8.8.4.4"],
        "syslog":       {"host": "syslog.internal", "port": 514, "protocol": "udp"},
        "packages":     {"install": ["vim", "curl"], "remove": []},
        "users": [
            {"name": "deploy",  "shell": "/bin/bash", "sudo": True},
            {"name": "monitor", "shell": "/bin/sh",   "sudo": False},
        ],
        "firewall": {
            "enabled": True,
            "default_policy": "deny",
            "allow_ports": [22, 80, 443],
        },
    }

    def test_operator_changed_syslog_host_playbook_changed_protocol(self):
        """Operator pointed syslog at local host; playbook switched to TCP."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        ours["syslog"]["host"]       = "syslog.datacenter-b.internal"
        theirs["syslog"]["protocol"] = "tcp"

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert conflicts == []
        assert merged["syslog"]["host"]     == "syslog.datacenter-b.internal"
        assert merged["syslog"]["protocol"] == "tcp"

    def test_playbook_adds_new_user_operator_modified_existing_user(self):
        """Playbook adds a new service account; operator gave deploy user a different shell."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        ours["users"][0]["shell"] = "/bin/zsh"      # operator changed deploy shell
        theirs["users"].append(
            {"name": "backup", "shell": "/bin/sh", "sudo": False}
        )

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert conflicts == []
        users = {u["name"]: u for u in merged["users"]}
        assert users["deploy"]["shell"] == "/bin/zsh"   # operator preserved
        assert "backup" in users                         # playbook addition applied

    def test_operator_opened_extra_port_playbook_changed_default_policy(self):
        """Two independent firewall changes — both apply."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        # operator added port 8080 — plain list all three different → conflict
        ours["firewall"]["allow_ports"] = [22, 80, 443, 8080]
        # playbook changed default_policy
        theirs["firewall"]["default_policy"] = "allow"

        merged, conflicts = merge(self.BASE, ours, theirs)
        # allow_ports: ours ≠ base, theirs = base → ours wins, no conflict
        assert merged["firewall"]["allow_ports"] == [22, 80, 443, 8080]
        assert merged["firewall"]["default_policy"] == "allow"
        assert conflicts == []

    def test_playbook_removes_deprecated_variable_operator_never_touched(self):
        """Playbook removes `ntp_servers` (superseded); operator never set it."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)
        theirs["ntp_chrony"] = ["0.pool.ntp.org"]   # new replacement key
        del theirs["ntp_servers"]                   # old key removed

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert conflicts == []
        assert "ntp_servers"  not in merged   # removal applied
        assert "ntp_chrony"   in merged       # new key added


# ── OpenAPI / Swagger spec ───────────────────────────────────────────────────
#
# API-first tools (e.g. openapi-generator) regenerate spec files from code
# annotations.  Teams add x-extensions, examples, and descriptions by hand.
# Re-generation must not erase those additions.


class TestOpenAPI:
    """Simulate OpenAPI spec regeneration while preserving developer extensions."""

    BASE = {
        "openapi": "3.0.3",
        "info": {"title": "My API", "version": "1.0.0", "description": ""},
        "paths": {
            "/users": {
                "get": {
                    "summary":     "",
                    "operationId": "listUsers",
                    "parameters":  [],
                    "responses": {
                        "200": {"description": "OK"},
                    },
                }
            }
        },
    }

    def test_developer_adds_examples_generator_adds_new_path(self):
        """Developer documented the GET; generator discovered a new POST endpoint."""
        ours = copy.deepcopy(self.BASE)
        ours["info"]["description"] = "User management service"
        ours["paths"]["/users"]["get"]["summary"] = "List all users"
        ours["paths"]["/users"]["get"]["responses"]["200"]["content"] = {
            "application/json": {"schema": {"type": "array"}}
        }

        theirs = copy.deepcopy(self.BASE)
        theirs["paths"]["/users"]["post"] = {
            "summary":     "",
            "operationId": "createUser",
            "responses":   {"201": {"description": "Created"}},
        }

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert conflicts == []
        assert merged["info"]["description"] == "User management service"
        assert "post" in merged["paths"]["/users"]
        assert merged["paths"]["/users"]["get"]["summary"] == "List all users"

    def test_generator_bumps_api_version_developer_also_bumped_it(self):
        """Both generator and developer bumped the API version — conflict."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        ours["info"]["version"]   = "1.1.0"   # developer manual bump
        theirs["info"]["version"] = "2.0.0"   # generator major bump

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert len(conflicts) == 1
        assert "version" in conflicts[0].path

    def test_developer_added_x_extension_generator_updated_schema(self):
        """Developer added vendor extension; generator updated a response schema."""
        ours = copy.deepcopy(self.BASE)
        ours["x-internal-id"] = "svc-users-001"   # vendor extension

        theirs = copy.deepcopy(self.BASE)
        theirs["paths"]["/users"]["get"]["responses"]["400"] = {
            "description": "Bad Request"
        }

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert conflicts == []
        assert merged["x-internal-id"] == "svc-users-001"
        assert "400" in merged["paths"]["/users"]["get"]["responses"]


# ── GitHub Actions workflow ───────────────────────────────────────────────────
#
# Organisations provide workflow templates.  Repository owners add custom
# steps (deploy targets, notification hooks).  Template updates must not
# remove those additions.


class TestGitHubActions:
    """Simulate GitHub Actions workflow template updates vs repo customisations."""

    BASE = {
        "name": "CI",
        "on": {
            "push":         {"branches": ["main"]},
            "pull_request": {"branches": ["main"]},
        },
        "jobs": {
            "test": {
                "runs-on": "ubuntu-latest",
                "strategy": {
                    "matrix": {"python-version": ["3.10", "3.11"]}
                },
                "steps": [
                    {"name": "Checkout",       "uses": "actions/checkout@v3"},
                    {"name": "Setup Python",   "uses": "actions/setup-python@v4",
                     "with": {"python-version": "${{ matrix.python-version }}"}},
                    {"name": "Install deps",   "run": "pip install -e .[dev]"},
                    {"name": "Run tests",      "run": "pytest"},
                ],
            }
        },
    }

    def test_template_adds_python_312_repo_added_deploy_job(self):
        """Template adds Python 3.12 to matrix; repo owner added a deploy job."""
        ours = copy.deepcopy(self.BASE)
        ours["jobs"]["deploy"] = {
            "needs": "test",
            "runs-on": "ubuntu-latest",
            "steps": [{"name": "Deploy", "run": "make deploy"}],
        }

        theirs = copy.deepcopy(self.BASE)
        theirs["jobs"]["test"]["strategy"]["matrix"]["python-version"] = [
            "3.10", "3.11", "3.12"
        ]

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert conflicts == []
        assert "deploy" in merged["jobs"]
        assert "3.12" in merged["jobs"]["test"]["strategy"]["matrix"]["python-version"]

    def test_template_bumps_actions_version_repo_added_env_var(self):
        """Template bumps checkout action; repo added an env var to test job."""
        ours = copy.deepcopy(self.BASE)
        ours["jobs"]["test"]["env"] = {"PYTHONPATH": "src"}

        theirs = copy.deepcopy(self.BASE)
        theirs["jobs"]["test"]["steps"][0]["uses"] = "actions/checkout@v4"

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert conflicts == []
        assert merged["jobs"]["test"]["env"] == {"PYTHONPATH": "src"}
        assert merged["jobs"]["test"]["steps"][0]["uses"] == "actions/checkout@v4"

    def test_both_changed_runs_on_is_conflict(self):
        """Template changed runner to arm64; repo changed it to self-hosted."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        ours["jobs"]["test"]["runs-on"]   = "self-hosted"
        theirs["jobs"]["test"]["runs-on"] = "ubuntu-latest-arm64"

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert len(conflicts) == 1
        assert "runs-on" in conflicts[0].path

    def test_repo_added_schedule_trigger_template_added_workflow_dispatch(self):
        """Repo added a cron trigger; template added manual dispatch — both should land."""
        ours = copy.deepcopy(self.BASE)
        ours["on"]["schedule"] = [{"cron": "0 6 * * 1"}]

        theirs = copy.deepcopy(self.BASE)
        theirs["on"]["workflow_dispatch"] = {}

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert conflicts == []
        assert "schedule"          in merged["on"]
        assert "workflow_dispatch" in merged["on"]


# ── Multi-tenant config management ───────────────────────────────────────────
#
# A SaaS platform generates per-tenant config files from a template.
# Customer success engineers customise individual tenants.  The platform
# rolls out new features as config additions.


class TestMultiTenantConfig:
    """Simulate SaaS platform config rollout vs CSM per-tenant overrides."""

    BASE = {
        "tenant_id":  "acme-corp",
        "tier":       "pro",
        "features": {
            "analytics":    True,
            "sso":          False,
            "audit_log":    True,
            "data_export":  False,
        },
        "limits": {
            "users":       100,
            "storage_gb":  50,
            "api_calls":   10000,
        },
        "notifications": {
            "email":   True,
            "slack":   False,
            "webhook": None,
        },
    }

    def test_csm_enabled_sso_platform_added_new_feature_flag(self):
        """CSM enabled SSO for the customer; platform rolled out a new feature flag."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        ours["features"]["sso"] = True                   # CSM override
        theirs["features"]["advanced_search"] = True      # new platform feature

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert conflicts == []
        assert merged["features"]["sso"] is True
        assert merged["features"]["advanced_search"] is True

    def test_csm_set_webhook_platform_also_set_it_conflict(self):
        """CSM and platform both set the webhook URL differently."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        ours["notifications"]["webhook"]   = "https://hooks.acme.com/custom"
        theirs["notifications"]["webhook"] = "https://platform.saas.io/default"

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert len(conflicts) == 1
        assert "webhook" in conflicts[0].path

    def test_platform_upgrade_increases_limits_csm_already_overrode_users(self):
        """Platform upgrade bumps storage and API limits; CSM had bumped users."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        ours["limits"]["users"]       = 250     # CSM negotiated more users
        theirs["limits"]["storage_gb"] = 100    # platform tier upgrade
        theirs["limits"]["api_calls"]  = 50000  # platform tier upgrade

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert conflicts == []
        assert merged["limits"]["users"]       == 250    # CSM override
        assert merged["limits"]["storage_gb"]  == 100   # platform upgrade
        assert merged["limits"]["api_calls"]   == 50000 # platform upgrade

    def test_platform_removes_deprecated_feature_csm_had_it_enabled(self):
        """Platform removed `analytics` (sunset); CSM had it explicitly enabled."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        ours["features"]["analytics"] = True    # CSM had explicitly set it (same as base)
        del theirs["features"]["analytics"]     # platform removed deprecated flag

        merged, conflicts = merge(self.BASE, ours, theirs)
        # ours == base for analytics → respect platform deletion
        assert "analytics" not in merged["features"]
        assert conflicts == []

    def test_tier_changed_by_both_is_conflict(self):
        """Billing upgraded tier to 'enterprise'; CSM had manually set 'pro-plus'."""
        ours   = copy.deepcopy(self.BASE)
        theirs = copy.deepcopy(self.BASE)

        ours["tier"]   = "pro-plus"    # CSM manual override
        theirs["tier"] = "enterprise"  # billing system upgrade

        merged, conflicts = merge(self.BASE, ours, theirs)
        assert len(conflicts) == 1
        assert conflicts[0].ours_val   == "pro-plus"
        assert conflicts[0].theirs_val == "enterprise"
