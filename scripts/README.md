# scripts

## What it does

Repository tooling that is not part of Burn itself.

## How it works

`install-hooks.sh` points git at `.githooks/`, whose pre-commit hook runs gitleaks on the staged
changes (with `.gitleaks.toml`, which adds rules for Burn's own credential formats) and refuses
any commit containing the AWS account id from your local `.env`.

## Key files

- `install-hooks.sh`: enables the pre-commit secret gate. Run once after cloning (`make hooks`).

## How to test it

```bash
make hooks
printf 'mpk_live_%s\n' "$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32)" > leak.txt
git add leak.txt && git commit -m test   # refused by gitleaks
git restore --staged leak.txt && rm leak.txt
```

## FAQ

**What if someone skips the hook?** The same gitleaks scan runs in CI on every pull request and
on every push to `main`, over the full history (`.github/workflows/secret-scan.yml`).
