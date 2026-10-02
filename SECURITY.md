# Security and publication notes

## Secrets

- Provide model credentials through environment variables or an untracked `.env` file.
- Never commit `config/pi/auth.json`, Bilibili credential files, or an Obsidian vault.
- Local run artifacts, transcripts, annotations, model outputs, and evaluation reports are excluded by `.gitignore` because they may contain private or copyrighted material.
- If a key is ever committed, revoke or rotate it immediately. Removing the file in a later commit does not remove the secret from Git history.

Before publishing, inspect the staged files and scan them for likely credentials. For example:

```powershell
git diff --cached --name-only
rg -n --hidden -g "!.git/**" -g "!.venv/**" "(api[_-]?key|secret|token|password)\s*[:=]"
```

The scan reports variable names and examples as well as real values, so review each match rather than assuming every result is a leak.
