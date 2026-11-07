# Burn: one entry point for setup, deploy, tests and the demo.
# Run `make` (or `make help`) to list targets.

SCENARIO ?= scenarios/support-desk.yaml
AGENT ?=
CDK := npx -y aws-cdk@2
export AWS_REGION := us-east-1
export AWS_DEFAULT_REGION := us-east-1

# Folders with a Python test suite (each has its own uv project).
PY_COMPONENTS := contracts detection minter burn_api agents

.DEFAULT_GOAL := help
.PHONY: help hooks deploy env test demo demo-step reset replay-old-key teardown

help: ## List targets
	@grep -E '^[a-zA-Z%_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

hooks: ## Install the gitleaks pre-commit secret gate
	./scripts/install-hooks.sh

deploy: ## Deploy every AWS stack (us-east-1)
	cd infra && uv sync -q && $(CDK) deploy --all --require-approval never

deploy-%: ## Deploy one stack, e.g. make deploy-BurnApi
	cd infra && uv sync -q && $(CDK) deploy $* --require-approval never

env: ## Write .env and dashboard/.env.local from the deployed stacks
	cd burn_api && uv run python scripts/make_env.py

test: ## Run every component's tests
	@set -e; for d in $(PY_COMPONENTS); do \
	  echo "== $$d"; \
	  if [ $$d = contracts ]; then (cd $$d && uv run -q --extra test pytest -q); \
	  else (cd $$d && uv run -q pytest -q); fi; \
	done

demo: ## Reset, mint fresh credentials and start the agents (SCENARIO=...)
	cd agents && uv run python scripts/run_demo.py --scenario $(SCENARIO)

demo-step: ## Same as demo, but the presenter advances one ticket at a time
	cd agents && uv run python scripts/run_demo.py --scenario $(SCENARIO) --step

reset: ## Clear demo state and delete the scenario's minted credentials
	cd agents && uv run python scripts/reset.py

replay-old-key: ## Replay a burned agent's pre-rotation keys (AGENT=agent-...)
	@test -n "$(AGENT)" || (echo "usage: make replay-old-key AGENT=agent-..." && exit 1)
	cd minter && uv run python scripts/replay_old_key.py --agent $(AGENT)

teardown: ## Destroy every stack and force-delete the system secrets (asks first)
	@read -p "Destroy all Burn stacks in this account? [y/N] " ok && [ "$$ok" = y ]
	cd agents && uv run python scripts/reset.py || true
	cd infra && $(CDK) destroy --all --force
	aws secretsmanager delete-secret --secret-id burn-system/fingerprint-key --force-delete-without-recovery || true
	aws secretsmanager delete-secret --secret-id burn-system/demo-token --force-delete-without-recovery || true
