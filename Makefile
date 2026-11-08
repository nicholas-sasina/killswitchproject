# Burn: one entry point for setup, deploy, tests and the demo.
# Run `make` (or `make help`) to list targets.

SCENARIO ?= scenarios/support-desk.yaml
AGENT ?=
ENCODING ?= random
ROLE ?= worker
N ?= 20
CDK := npx -y aws-cdk@2
export AWS_REGION := us-east-1
export AWS_DEFAULT_REGION := us-east-1

# Folders with a Python test suite (each has its own uv project).
PY_COMPONENTS := contracts detection gateway minter burn_api agents mocks eval cli

.DEFAULT_GOAL := help
.PHONY: burn help hooks deploy env test test-dashboard demo demo-step reset replay-old-key e2e dashboard mocks exit-check-test proxy-env eval swarm teardown

help: ## List targets
	@grep -E '^[a-zA-Z0-9%_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

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
	  case $$d in \
	    contracts) (cd $$d && uv run -q --extra test pytest -q) ;; \
	    detection) (cd $$d && uv run -q --python 3.12 --extra proxy --extra leaktest pytest -q) ;; \
	    *) (cd $$d && uv run -q pytest -q) ;; \
	  esac; \
	done

test-dashboard: ## Run the dashboard's tests and production build
	cd dashboard && npm ci --silent && npm test && npm run build

demo: ## Reset, mint fresh credentials and start the agents (SCENARIO=...)
	cd agents && uv run python scripts/run_demo.py --scenario $(SCENARIO)

demo-step: ## Same as demo, but the presenter advances one ticket at a time
	cd agents && uv run python scripts/run_demo.py --scenario $(SCENARIO) --step

reset: ## Clear demo state and delete the scenario's minted credentials
	cd agents && uv run python scripts/reset.py

replay-old-key: ## Replay a burned agent's pre-rotation keys (AGENT=agent-...)
	@test -n "$(AGENT)" || (echo "usage: make replay-old-key AGENT=agent-..." && exit 1)
	cd minter && uv run python scripts/replay_old_key.py --agent $(AGENT)

e2e: ## Live containment check against the deployed stack (AGENT=agent-... optional)
	cd burn_api && uv run python scripts/e2e.py $(AGENT)

dashboard: ## Start the dashboard on http://localhost:5173 (run make env first)
	cd dashboard && npm ci --silent && npm run dev

mocks: ## Start the mock Burn API (:8700) and mock MCP tools (:8701), development only
	cd mocks && (uv run python burn_api_mock.py & uv run python mcp_tools_mock.py & wait)

proxy-env: ## Write .run/proxy.env with the egress proxy's own AWS identity
	cd detection && uv run --python 3.12 --extra proxy python scripts/proxy_env.py

swarm: ## Start N deterministic workers under `burn run` on the demo network (ROLE=worker N=20, MOCK=1 for the mock servers)
	cd agents && uv run python scripts/run_swarm.py --role $(ROLE) --count $(N) $(if $(MOCK),--mock)

burn: ## Run the burn CLI, e.g. make burn ARGS="agents"
	cd cli && uv run burn $(ARGS)

exit-check-test: ## Verify the exit check once (AGENT=agent-... ENCODING=base64 optional)
	@test -n "$(AGENT)" || (echo "usage: make exit-check-test AGENT=agent-... [ENCODING=base64]" && exit 1)
	@result=$$(cd detection && BURN_PROXY_CA_CERT="$(CURDIR)/.run/proxy-certs/mitmproxy-ca-cert.pem" uv run --python 3.12 --env-file ../.env --extra leaktest python -m leaktest --agent "$(AGENT)" --encoding "$(ENCODING)") && \
	  printf '%s\n' "$$result" | python3 -c 'import json, sys; result = json.load(sys.stdin); print(("blocked" if result["blocked"] else "allowed") + ": " + (result.get("reason") or "no block reason returned"))'

eval: ## Rerun the offline eval and rewrite eval/results.json and eval/RESULTS.md
	cd eval && uv run python run_offline.py --trials 10

teardown: ## Destroy every stack and force-delete the system secrets (asks first)
	@read -p "Destroy all Burn stacks in this account? [y/N] " ok && [ "$$ok" = y ]
	cd agents && uv run python scripts/reset.py || true
	cd infra && $(CDK) destroy --all --force
	aws secretsmanager delete-secret --secret-id burn-system/fingerprint-key --force-delete-without-recovery || true
	aws secretsmanager delete-secret --secret-id burn-system/demo-token --force-delete-without-recovery || true
