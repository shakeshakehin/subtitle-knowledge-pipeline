# Security and publication notes

## Secrets

- Provide model credentials through environment variables or an untracked `.env` file.
- Never commit `config/pi/auth.json`, Bilibili credential files, or an Obsidian vault.
- Local run artifacts, transcripts, annotations, model outputs, and evaluation reports are excluded by `.gitignore` because they may contain private or copyrighted material.
- If a key is ever committed, revoke or rotate it immediately. Removing the file in a later commit does not remove the secret from Git history.

## Internet exposure

- Do not expose the development server directly with router port forwarding.
- Use `serve --public-mode` behind an HTTPS reverse proxy or tunnel. Public mode requires a unique 16+ character access password.
- Public mode locks model endpoints, report providers, and the Obsidian output root to server-controlled values. This prevents a remote request from redirecting a server-configured API key to an arbitrary endpoint or selecting an arbitrary write path.
- The protected-preview mode is single-user. It is not a replacement for per-user accounts, quotas, storage isolation, abuse prevention, and retention controls required by an anonymous public service.

Before publishing, inspect the staged files and scan them for likely credentials. For example:

```powershell
git diff --cached --name-only
rg -n --hidden -g "!.git/**" -g "!.venv/**" "(api[_-]?key|secret|token|password)\s*[:=]"
```

The scan reports variable names and examples as well as real values, so review each match rather than assuming every result is a leak.
